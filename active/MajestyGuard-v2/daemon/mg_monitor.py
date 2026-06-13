"""
mg_monitor.py — Lightweight MajestyGuard idle-watcher daemon.

Imports ONLY stdlib + ctypes to keep RSS under 10MB.
Watches keyboard/mouse idle via GetLastInputInfo(). When idle exceeds
MG_IDLE_TIMEOUT, launches the full daemon (main.py). When the full daemon
exits (owner verified), resumes watching.

Mutual watchdog: if the full daemon dies while lock_state.txt says LOCKED,
relaunch it immediately.

Usage:
    pythonw daemon/mg_monitor.py
"""
import sys
import os
import time
import subprocess
import ctypes
import ctypes.wintypes
import signal
import pathlib
import logging

log = logging.getLogger("majestyguard.monitor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ── Constants ──────────────────────────────────────────────────────────────

MIN_IDLE_TIMEOUT_S = 15.0
MAX_IDLE_TIMEOUT_S = 600.0
DEFAULT_IDLE_TIMEOUT_S = 90.0
POLL_INTERVAL_S = 2.0

_DAEMON_DIR = pathlib.Path(__file__).resolve().parent
_MG_STATE_DIR = pathlib.Path(
    os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData"))
) / "MajestyGuard"

MONITOR_PID_FILE = _MG_STATE_DIR / "monitor.pid"
DAEMON_PID_FILE = _MG_STATE_DIR / "daemon.pid"
LOCK_STATE_FILE = _MG_STATE_DIR / "lock_state.txt"


# ── Idle detection (ctypes, no pywin32) ────────────────────────────────────

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwTime", ctypes.wintypes.DWORD),
    ]


def get_idle_seconds() -> float:
    """Seconds since last keyboard/mouse input."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    elapsed_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, float(elapsed_ms) / 1000.0)


def read_idle_timeout() -> float:
    """Read MG_IDLE_TIMEOUT from environment, clamped to 15-600s."""
    raw = os.environ.get("MG_IDLE_TIMEOUT") or os.environ.get("MG_SOFT_LOCK_IDLE_SECONDS")
    if raw is None or raw.strip() == "":
        return DEFAULT_IDLE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        log.warning("Invalid MG_IDLE_TIMEOUT=%r; using %.0fs", raw, DEFAULT_IDLE_TIMEOUT_S)
        return DEFAULT_IDLE_TIMEOUT_S
    clamped = max(MIN_IDLE_TIMEOUT_S, min(MAX_IDLE_TIMEOUT_S, value))
    if clamped != value:
        log.warning("MG_IDLE_TIMEOUT=%.0fs outside 15-600s; using %.0fs", value, clamped)
    return clamped


# ── PID helpers ────────────────────────────────────────────────────────────

def _write_pid(path: pathlib.Path, pid: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="utf-8")
    except OSError as e:
        log.warning("Failed to write %s: %s", path.name, e)


def _read_pid(path: pathlib.Path) -> int:
    """Read PID from file, return 0 if missing/invalid."""
    try:
        if not path.exists():
            return 0
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else 0
    except (OSError, ValueError):
        return 0


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def _read_lock_state() -> str:
    """Read lock_state.txt, return 'UNLOCKED' if missing."""
    try:
        if LOCK_STATE_FILE.exists():
            return LOCK_STATE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return "UNLOCKED"


# ── Daemon launcher ───────────────────────────────────────────────────────

_DAEMON_LOG_PATH = _MG_STATE_DIR / "daemon_stdout.log"


def _launch_full_daemon() -> subprocess.Popen:
    """Launch main.py as the full recognition daemon."""
    daemon_script = _DAEMON_DIR / "main.py"
    python = sys.executable
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["MG_FORCE_LOCK_STARTUP"] = "1"
    env["MG_OVERLAY_WATCHDOG"] = "1"
    log.info("Launching full daemon: %s %s", python, daemon_script)
    log_fh = open(_DAEMON_LOG_PATH, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [python, str(daemon_script)],
        cwd=str(_DAEMON_DIR.parent),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.info("Full daemon launched (PID %d), log: %s", proc.pid, _DAEMON_LOG_PATH)
    return proc


# ── Main monitor loop ─────────────────────────────────────────────────────

_WATCHDOG_COOLDOWN_S = 10.0


class MonitorDaemon:
    def __init__(self):
        self._stop = False
        self._idle_timeout = read_idle_timeout()
        self._daemon_proc = None
        self._idle_fired = False
        self._last_watchdog_launch = 0.0

    def run(self) -> None:
        _MG_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _write_pid(MONITOR_PID_FILE, os.getpid())
        log.info(
            "MajestyGuard monitor started (PID %d, idle_timeout=%.0fs)",
            os.getpid(),
            self._idle_timeout,
        )

        def _sig_handler(sig, frame):
            log.info("Signal %d received — stopping monitor", sig)
            self._stop = True

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        try:
            self._loop()
        finally:
            self._cleanup()

    def _loop(self) -> None:
        while not self._stop:
            self._tick()
            time.sleep(POLL_INTERVAL_S)

    def _tick(self) -> None:
        daemon_running = self._is_daemon_running()

        # ── Watchdog: relaunch daemon if it died while LOCKED ──────────
        if not daemon_running and _read_lock_state() == "LOCKED":
            daemon_pid = _read_pid(DAEMON_PID_FILE)
            if daemon_pid > 0 and not _is_pid_alive(daemon_pid):
                now = time.monotonic()
                elapsed = now - self._last_watchdog_launch
                if elapsed < _WATCHDOG_COOLDOWN_S:
                    log.debug(
                        "[Watchdog] Daemon died while LOCKED — cooldown (%.0fs left)",
                        _WATCHDOG_COOLDOWN_S - elapsed,
                    )
                    return
                log.warning("[Watchdog] Daemon died while LOCKED — relaunching (cooldown %.0fs)", _WATCHDOG_COOLDOWN_S)
                self._last_watchdog_launch = now
                self._daemon_proc = _launch_full_daemon()
                return

        # ── Normal idle monitoring ─────────────────────────────────────
        idle_seconds = get_idle_seconds()

        if idle_seconds < self._idle_timeout:
            self._idle_fired = False
            return

        if daemon_running:
            return  # Daemon already handling it

        if self._idle_fired:
            return  # Already fired this idle stretch

        # Idle timeout reached — launch full daemon
        self._idle_fired = True
        log.info(
            "Idle timeout reached (%.0fs >= %.0fs) — launching full daemon",
            idle_seconds, self._idle_timeout,
        )
        self._daemon_proc = _launch_full_daemon()

    def _is_daemon_running(self) -> bool:
        """Check if our launched daemon process is still running."""
        if self._daemon_proc is None:
            return False
        ret = self._daemon_proc.poll()
        if ret is not None:
            log.info("Full daemon exited (code=%s) — resuming idle watch", ret)
            self._daemon_proc = None
            self._idle_fired = False  # Reset so next idle stretch can fire
            return False
        return True

    def _cleanup(self) -> None:
        log.info("Monitor daemon shutting down")
        try:
            if MONITOR_PID_FILE.exists():
                MONITOR_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        # Kill daemon if we're exiting
        if self._daemon_proc is not None and self._daemon_proc.poll() is None:
            log.info("Terminating full daemon on monitor exit")
            self._daemon_proc.terminate()
            try:
                self._daemon_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._daemon_proc.kill()


def main() -> None:
    daemon = MonitorDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
