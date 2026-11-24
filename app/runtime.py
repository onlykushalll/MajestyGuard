"""Process supervision for the local MajestyGuard daemon."""
from __future__ import annotations

import os
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

from app.config import (
    MAX_IDLE_TIMEOUT_SECONDS,
    MIN_IDLE_TIMEOUT_SECONDS,
    RuntimeSettings,
    load_settings,
    save_settings,
)


class RuntimeStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    DEGRADED = "degraded"


# A daemon that dies unexpectedly is restarted with growing pauses; too many deaths in a short
# window means something is really wrong (bad camera driver, corrupt install), so stop and say so
# instead of restarting forever.
RECOVERY_DELAYS_S = (2.0, 5.0, 15.0, 45.0, 120.0)
CRASH_WINDOW_S = 600.0
MAX_CRASHES_IN_WINDOW = 6
STABLE_AFTER_S = 120.0


class Supervisor:
    """Own the daemon lifecycle without becoming a biometric decision-maker."""

    def __init__(
        self,
        root: Path,
        python_executable: Path,
        settings_path: Path,
        *,
        local_app_data: Optional[Path] = None,
        daemon_executable: Optional[Path] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = Path(root)
        self._python = Path(python_executable)
        self._settings_path = Path(settings_path)
        self._local_app_data = Path(local_app_data) if local_app_data else Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
        self._daemon_executable = Path(daemon_executable) if daemon_executable else None
        self._process: Optional[subprocess.Popen] = None
        self._enrollment_process: Optional[subprocess.Popen] = None
        self._resume_after_enrollment: bool = False
        self._clock = clock
        self._started_at: Optional[float] = None
        self._crashes: list[float] = []
        self._noted_dead: Optional[subprocess.Popen] = None
        self._next_restart_at: float = 0.0
        self._gave_up = False
        self._last_recovery_at: Optional[float] = None

    def status(self) -> RuntimeStatus:
        if self._enrollment_process is not None and self._enrollment_process.poll() is not None:
            self._enrollment_process = None
            if self._resume_after_enrollment and self.enrollment_present():
                self._resume_after_enrollment = False
                self.start()
        if self._process is None:
            return RuntimeStatus.PAUSED if load_settings(self._settings_path).paused else RuntimeStatus.STOPPED
        return RuntimeStatus.RUNNING if self._process.poll() is None else RuntimeStatus.DEGRADED

    def enrollment_present(self) -> bool:
        state_dir = self._local_app_data / "MajestyGuard"
        return (state_dir / "embeddings_v2.npy").is_file() or (state_dir / "biometric.mgd").is_file()

    def start(self) -> bool:
        """Start protection on the user's request (forgives earlier crashes)."""
        started = self._launch()
        if started:
            self._crashes.clear()
            self._gave_up = False
        return started

    def maintain(self) -> str:
        """Keep protection alive. Call every couple of seconds.

        Returns '' (nothing to do), 'waiting' (restart scheduled), 'restarted' or 'gave_up'.
        Never restarts anything the user stopped or paused, and never without an enrollment.
        """
        process = self._process
        if process is None:
            return ""
        now = self._clock()
        if process.poll() is None:
            if self._started_at is not None and now - self._started_at >= STABLE_AFTER_S:
                self._crashes.clear()
                self._gave_up = False
            return ""
        if load_settings(self._settings_path).paused or not self.enrollment_present():
            return ""
        if self._gave_up:
            return "gave_up"
        if self._noted_dead is not process:
            self._noted_dead = process
            self._crashes = [c for c in self._crashes if now - c < CRASH_WINDOW_S] + [now]
            if len(self._crashes) > MAX_CRASHES_IN_WINDOW:
                self._gave_up = True
                return "gave_up"
            delay = RECOVERY_DELAYS_S[min(len(self._crashes), len(RECOVERY_DELAYS_S)) - 1]
            self._next_restart_at = now + delay
        if now < self._next_restart_at:
            return "waiting"
        if self._launch():
            self._last_recovery_at = now
            return "restarted"
        self._next_restart_at = now + RECOVERY_DELAYS_S[-1]
        return "waiting"

    def recovery_note(self) -> str:
        """Plain-language line for the UI, or ''."""
        if self._gave_up:
            return "Protection keeps stopping. Open diagnostics, or restart it yourself."
        if self._last_recovery_at is not None and self._clock() - self._last_recovery_at < CRASH_WINDOW_S:
            return "Protection stopped unexpectedly and was restarted automatically."
        return ""

    def _launch(self) -> bool:
        if self.status() in (RuntimeStatus.STARTING, RuntimeStatus.RUNNING):
            return False

        source_entrypoint = self._root / "daemon" / "main.py"
        daemon_command: list[str]
        packaged_daemon = self._packaged_daemon_executable()
        if packaged_daemon is not None:
            daemon_command = [str(packaged_daemon)]
        elif source_entrypoint.is_file():
            daemon_command = [str(self._python), str(source_entrypoint)]
        else:
            return False

        settings = load_settings(self._settings_path)
        if settings.paused:
            return False

        environment = dict(os.environ)
        environment.update(
            {
                "MG_ENABLE_LOCK": "1" if settings.enable_windows_lock else "0",
                "MG_MAX_SECONDS": "0",
                "MG_OVERLAY_WATCHDOG": "1",
                "MG_IDLE_TIMEOUT": str(settings.idle_timeout_seconds),
                "MG_PASSIVE_FPS": "0",
                "MG_ENABLE_WHCDF_IPC": "0",
                "MG_ENABLE_SERVICE_IPC": "0",
                "MG_ADAFACE_FLIP_FUSION": "1",
                "MG_IDLE_CHECK_MODE": "1",
                "MG_PERSISTENT_IDLE_LOOP": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        packaged_ui = self._packaged_ui_executable()
        if packaged_ui is not None:
            environment["MG_UI_EXECUTABLE"] = str(packaged_ui)
        try:
            self._process = self._spawn(daemon_command, environment)
        except OSError:
            self._process = None
            return False
        self._started_at = self._clock()
        return True

    def session_locked(self) -> bool:
        """True while the daemon reports the overlay lock as engaged."""
        try:
            text = (self._settings_path.parent / "lock_state.txt").read_text(encoding="utf-8")
        except OSError:
            return False
        return text.strip().upper() == "LOCKED"

    def controls_locked(self) -> bool:
        """Lifecycle and settings controls are unavailable while locked.

        A lock that can be paused, stopped or reconfigured from the unlocked
        desktop tools is not a lock, so every control that could weaken
        protection refuses while the session is locked. A daemon this
        supervisor knows is dead cannot be holding a lock, so a stale
        LOCKED file does not strand the user.
        """
        if not self.session_locked():
            return False
        process = self._process
        if process is not None and process.poll() is not None:
            return False
        return True

    def stop(self, timeout_seconds: float = 5.0) -> bool:
        """Stop the daemon. Returns False (and does nothing) while locked."""
        if self.controls_locked():
            return False
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            return True
        # The lock may have engaged between the check above and now; look again
        # immediately before the irreversible step.
        if self.controls_locked():
            return False
        self._process = None
        process.terminate()
        try:
            process.wait(timeout=max(0.1, float(timeout_seconds)))
        except subprocess.TimeoutExpired:
            process.kill()
        return True

    def _clear_lock_state(self) -> None:
        """After an allowed pause nothing can enforce a stale LOCKED marker."""
        path = self._settings_path.parent / "lock_state.txt"
        try:
            if path.is_file():
                tmp_path = path.with_name(path.name + ".tmp")
                tmp_path.write_text("UNLOCKED\n", encoding="utf-8")
                os.replace(tmp_path, path)
        except OSError:
            pass

    def pause(self) -> bool:
        if self.controls_locked():
            return False
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=True,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        if not self.stop():
            save_settings(self._settings_path, settings)   # lock engaged mid-pause: undo
            return False
        self._clear_lock_state()
        return True

    def resume(self) -> bool:
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=False,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        return self.start()

    def set_idle_timeout(self, seconds: int) -> bool:
        if self.controls_locked():
            return False
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            return False
        if not MIN_IDLE_TIMEOUT_SECONDS <= seconds <= MAX_IDLE_TIMEOUT_SECONDS:
            return False
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=seconds,
                paused=settings.paused,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        return True

    def windows_lock_enabled(self) -> bool:
        return load_settings(self._settings_path).enable_windows_lock

    def set_windows_lock_enabled(self, enabled: bool) -> bool:
        if self.controls_locked():
            return False
        if not isinstance(enabled, bool):
            return False
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=settings.paused,
                enable_windows_lock=enabled,
            ),
        )
        if self.status() is RuntimeStatus.RUNNING:
            self.stop()
            self.start()
        return True

    def start_enrollment(self, *, start_over: bool = False) -> bool:
        """Launch the guided enrollment tool at most once at a time.

        By default a new face may only replace the enrolled one if it is recognisably the same
        person. ``start_over`` waives that, and the caller must have re-authenticated the user
        with Windows first (see app.reauth). The setting is always passed explicitly so a stray
        MG_ENROLL_CONTINUITY in this process's own environment cannot weaken the check.
        """
        if self._enrollment_process is not None and self._enrollment_process.poll() is None:
            return False

        # Resolve what to launch BEFORE touching the running daemon, so a
        # missing enrollment tool cannot leave protection switched off.
        packaged_daemon = self._packaged_daemon_executable()
        if packaged_daemon is not None and packaged_daemon.is_file():
            cmd = [str(packaged_daemon), "--enroll"]
        else:
            entrypoint = self._root / "daemon" / "enroll_v2.py"
            if not entrypoint.is_file():
                return False
            cmd = [str(self._python), str(entrypoint)]

        if self.controls_locked():
            return False
        was_running = self.status() in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING)
        if was_running:
            if not self.stop():
                return False
            self._resume_after_enrollment = True

        environment = dict(os.environ)
        environment.update(
            {
                "MG_ENABLE_LOCK": "0",
                "MG_ENABLE_WHCDF_IPC": "0",
                "MG_ENABLE_SERVICE_IPC": "0",
                "MG_ENROLL_CONTINUITY": "0" if start_over else "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        try:
            self._enrollment_process = self._spawn_enrollment(
                cmd,
                environment,
            )
        except OSError:
            self._enrollment_process = None
            if was_running:
                # Nothing will ever signal completion; put protection back now.
                self._resume_after_enrollment = False
                self.start()
            return False
        return True

    def open_log_directory(self) -> Path:
        """Return the local diagnostic directory without exposing biometric data."""
        path = self._local_app_data / "MajestyGuard"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _spawn(self, command: Sequence[str], environment: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            list(command),
            cwd=str(self._root),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _spawn_enrollment(self, command: Sequence[str], environment: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            list(command),
            cwd=str(self._root),
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )

    def _packaged_daemon_executable(self) -> Optional[Path]:
        candidates = (
            self._daemon_executable,
            self._root / "MajestyGuardDaemon.exe",
            self._root / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        )
        return next((candidate for candidate in candidates if candidate is not None and candidate.is_file()), None)

    def _packaged_ui_executable(self) -> Optional[Path]:
        candidates = (
            self._root / "MajestyGuardUI.exe",
            self._root / "MajestyGuardUI" / "MajestyGuardUI.exe",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)
