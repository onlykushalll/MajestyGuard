r"""
main.py — MajestyGuard Dynamic Island UI entry point.

Reads state JSON from \\.\pipe\\MajestyGuard_UI and drives IslandWidget.
Run AFTER the daemon is started (daemon creates the pipe).

Usage:
    python ui/main.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))
from island import IslandWidget
from soft_lock import SoftLockOverlay
from states import get_state, COMMAND_PIPE_NAME, PIPE_NAME
from verification_feedback import request_verification_with_feedback

log = logging.getLogger("MajestyGuard.UI")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


# ── IPC bridge ────────────────────────────────────────────────────────────────

class StateSignal(QObject):
    state_changed = pyqtSignal(object)


class PipeReader(threading.Thread):
    """
    Background thread: polls MajestyGuard_UI named pipe.
    Each connection delivers one JSON line then closes.
    Emits via StateSignal → Qt main thread (thread-safe).
    """

    def __init__(self, signal_obj: StateSignal, stop_event: threading.Event):
        super().__init__(name="mg-ui-pipe", daemon=True)
        self._sig  = signal_obj
        # NOT ``_stop``: that name belongs to threading.Thread internals and
        # shadowing it breaks Thread.join().
        self._stop_event = stop_event
        self._last_state_dict = {}
        self._pending = b""

    def run(self) -> None:
        GENERIC_READ  = 0x80000000
        OPEN_EXISTING = 3
        INVALID       = 18446744073709551615
        k32           = ctypes.windll.kernel32

        log.info("Pipe reader started — connecting to %s", PIPE_NAME)

        while not self._stop_event.is_set():
            # Wait up to 2s for pipe to become available
            available = k32.WaitNamedPipeW(ctypes.create_unicode_buffer(PIPE_NAME), 2000)
            if not available:
                time.sleep(0.3)
                continue

            handle = k32.CreateFileW(
                PIPE_NAME, GENERIC_READ,
                0, None, OPEN_EXISTING, 0, None
            )
            if handle in (-1, 0, INVALID):
                time.sleep(0.3)
                continue


            self._pending = b""
            try:
                while not self._stop_event.is_set():
                    buf        = ctypes.create_string_buffer(4096)
                    bytes_read = ctypes.wintypes.DWORD(0)
                    ok = k32.ReadFile(handle, buf, ctypes.sizeof(buf) - 1,
                                      ctypes.byref(bytes_read), None)
                    if not ok or bytes_read.value == 0:
                        break  # Server disconnected or pipe closed
                    self.feed(buf.raw[:bytes_read.value])
            except Exception as e:
                log.debug("Pipe connection error: %s", e)
            finally:
                k32.CloseHandle(handle)

    def feed(self, chunk: bytes) -> None:
        """Accept raw pipe bytes; emit one state per complete newline-
        terminated line. A line split across two reads (or a UTF-8 character
        split at a read boundary) is held until its remainder arrives."""
        self._pending += chunk
        if len(self._pending) > 65536 and b"\n" not in self._pending:
            self._pending = b""  # runaway/garbage guard; resync on next line
            return
        *lines, self._pending = self._pending.split(b"\n")
        for line in lines:
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._parse_and_emit(text)

    def _parse_and_emit(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                log.warning("Ignoring non-object state payload: %r", raw[:80])
                return
            if data == self._last_state_dict:
                return
            self._last_state_dict = data

            state = get_state(
                data.get("state", "idle"),
                data.get("confidence"),
                data.get("liveness"),
                data.get("progress"),
                data.get("quality"),
                data.get("face_position"),
                data.get("detail", ""),
            )
            self._sig.state_changed.emit(state)
            log.debug("State: %s", data.get("state"))
        except json.JSONDecodeError as e:
            log.warning("Bad JSON from pipe: %r — %s", raw[:80], e)
        except (TypeError, ValueError) as e:
            # e.g. a non-numeric metric: drop this one update, keep the pipe.
            log.warning("Unusable state payload %r — %s", raw[:80], e)


# ── Main ──────────────────────────────────────────────────────────────────────

class CommandWriter:
    """Best-effort UI-to-daemon command sender."""

    def __init__(self):
        self._last_verify_at = 0.0

    def verify_requested(self, source: str) -> bool:
        now = time.monotonic()
        if now - self._last_verify_at < 0.35:
            return False
        self._last_verify_at = now
        payload = json.dumps({"cmd": "verify_requested", "source": source}, separators=(",", ":")) + "\n"
        return self._write_command(payload)

    def emergency_lock(self, source: str = "ui") -> bool:
        payload = json.dumps({"cmd": "emergency_lock", "source": source}, separators=(",", ":")) + "\n"
        return self._write_command(payload)

    def windows_lock_used(self, source: str = "ui") -> bool:
        payload = json.dumps({"cmd": "windows_lock_used", "source": source}, separators=(",", ":")) + "\n"
        return self._write_command(payload)

    def _write_command(self, payload: str) -> bool:
        """Write a command to the daemon's pipe and report whether it
        actually reached the pipe. The blocking Win32 I/O still runs on a
        background thread (WaitNamedPipeW alone can take up to 250ms,
        which would visibly stall the UI thread) but this now waits
        briefly for the real outcome instead of assuming success —
        callers that show UI feedback based on this return value need
        that feedback to be honest, especially when the daemon isn't
        actually running to receive the command."""
        result: dict[str, bool] = {"ok": False}
        done = threading.Event()

        def _run() -> None:
            try:
                result["ok"] = self._sync_write_command(payload)
            except Exception:
                log.debug("Command pipe write raised", exc_info=True)
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True).start()
        # Slightly above the pipe's own 250ms connect timeout so a slow-
        # but-working pipe isn't misreported as failed.
        done.wait(timeout=0.4)
        return result["ok"]

    def _sync_write_command(self, payload: str) -> bool:
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        INVALID = 18446744073709551615
        k32 = ctypes.windll.kernel32

        k32.WaitNamedPipeW(ctypes.create_unicode_buffer(COMMAND_PIPE_NAME), 250)
        handle = k32.CreateFileW(
            COMMAND_PIPE_NAME,
            GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle in (-1, 0, INVALID):
            log.debug("Command pipe unavailable")
            return False
        try:
            data = payload.encode("utf-8")
            written = ctypes.wintypes.DWORD(0)
            ok = k32.WriteFile(
                handle,
                ctypes.create_string_buffer(data),
                len(data),
                ctypes.byref(written),
                None,
            )
            if not ok:
                log.debug("Command pipe write failed")
            return bool(ok)
        finally:
            k32.CloseHandle(handle)



def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    command_writer = CommandWriter()
    disable_soft_lock_overlay = os.environ.get("MG_DISABLE_SOFT_LOCK_OVERLAY", "0") == "1"
    shield: Optional[SoftLockOverlay] = None
    widget: Optional[IslandWidget] = None

    def _request_verification(source: str) -> None:
        if widget is None:
            return
        if not request_verification_with_feedback(command_writer, shield, widget, source):
            log.warning(
                "Verify-now request from %r did not reach the daemon (pipe unavailable) "
                "- no UI feedback was shown, since showing 'verifying' here would be "
                "false. If this repeats, the daemon is likely not running.",
                source,
            )

    shield = None if disable_soft_lock_overlay else SoftLockOverlay(
        on_verify_requested=_request_verification,
        on_windows_lock_used=command_writer.windows_lock_used
    )
    widget = IslandWidget(
        on_verify_requested=_request_verification,
        on_overlay_dissolve=shield.dissolve if shield is not None else None,
    )
    if shield is not None:
        widget.setParent(shield, widget.windowFlags())
    widget.show()
    widget.apply_state(get_state("idle"))
    # apply_state() above unconditionally sets _alpha back to 1.0, so the
    # placeholder "idle" pill (a real 120x28 dark rounded rect, not
    # actually invisible -- confirmed by reading states.py) becomes
    # genuinely visible on screen the instant this process starts, before
    # any real state has ever arrived from the daemon. Contradicts the
    # documented spec ("Idle/hidden when not needed") and is very likely
    # what's been perceived as "the Dynamic Island appears too early" --
    # this is a UI-side cause independent of whatever the daemon's own
    # pre-warm/idle-detection timing does. Force it invisible immediately
    # after; the very next real apply_state() call (idle_detected,
    # scanning, etc.) resets _alpha to 1.0 on its own and makes the pill
    # visible again at the actually-correct moment -- no risk of this
    # getting stuck invisible.
    widget._alpha = 0.0
    widget.update()
    widget.hide()

    try:
        from soft_lock import recover_gestures_after_crash
        recover_gestures_after_crash()
    except Exception:
        pass

    sig        = StateSignal()
    stop_event = threading.Event()
    reader     = PipeReader(sig, stop_event)

    def _apply_state(state):
        if state.name == "exit":
            log.info("Exit state received — shutting down UI cleanly")
            _shutdown()
            return
        if shield is not None:
            shield.apply_state(state)
        widget.apply_state(state)
        widget.raise_()

    sig.state_changed.connect(_apply_state)
    reader.start()
    log.info("MajestyGuard Dynamic Island running")

    def _shutdown(*_):
        log.info("Shutting down UI")
        stop_event.set()
        try:
            from soft_lock import _uninstall_hooks, _set_taskbar_visible
            _uninstall_hooks()
            _set_taskbar_visible(True)
        except Exception:
            pass
        app.quit()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Exit when the daemon that launched this UI is gone (see parent_watch).
    from parent_watch import OrphanGuard, configured_daemon_pid, quit_flag_path, quit_requested

    guard = OrphanGuard(configured_daemon_pid())
    orphan_timer = QTimer()
    orphan_timer.setInterval(3000)

    def _check_orphan() -> None:
        visible = bool(shield is not None and shield.isVisible())
        if guard.should_exit(visible):
            log.warning("Daemon %s is gone - UI exiting", guard.daemon_pid)
            _shutdown()

    quit_path = quit_flag_path()

    def _check_quit_request() -> None:
        # Deliberate stop by the daemon (it is unlocking or exiting): shut down through the
        # normal path so the taskbar, cursor clip and hooks are restored, unlike terminate().
        if quit_requested(quit_path, bool(shield is not None and shield.isVisible())):
            log.info("Daemon asked the UI to quit")
            _shutdown()

    if quit_path:
        quit_timer = QTimer()
        quit_timer.setInterval(250)
        quit_timer.timeout.connect(_check_quit_request)
        quit_timer.start()
    orphan_timer.timeout.connect(_check_orphan)
    if guard.daemon_pid is not None:
        orphan_timer.start()

    # Let Ctrl+C propagate through Qt event loop
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        # All UI modules have imported, but no window, IPC client or hooks exist.
        raise SystemExit(0)
    main()
