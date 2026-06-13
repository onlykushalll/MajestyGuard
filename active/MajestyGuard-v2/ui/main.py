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
        self._stop = stop_event
        self._last_state_dict = {}

    def run(self) -> None:
        GENERIC_READ  = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        INVALID       = ctypes.c_void_p(-1).value
        k32           = ctypes.windll.kernel32

        log.info("Pipe reader started — connecting to %s", PIPE_NAME)

        while not self._stop.is_set():
            # Wait up to 2s for pipe to become available
            available = k32.WaitNamedPipeW(ctypes.create_unicode_buffer(PIPE_NAME), 2000)
            if not available:
                time.sleep(0.3)
                continue

            handle = k32.CreateFileW(
                PIPE_NAME, GENERIC_READ | GENERIC_WRITE,
                0, None, OPEN_EXISTING, 0, None
            )
            if handle == INVALID:
                time.sleep(0.3)
                continue

            try:
                buf        = ctypes.create_string_buffer(4096)
                bytes_read = ctypes.wintypes.DWORD(0)
                ok = k32.ReadFile(handle, buf, ctypes.sizeof(buf),
                                  ctypes.byref(bytes_read), None)
                if ok and bytes_read.value > 0:
                    raw = buf.raw[:bytes_read.value].decode("utf-8").strip()
                    self._parse_and_emit(raw)
            except Exception as e:
                log.debug("Pipe read error: %s", e)
            finally:
                k32.CloseHandle(handle)
            
            # Limit polling frequency to ~60 Hz to prevent tight loop CPU thrashing
            time.sleep(0.016)

    def _parse_and_emit(self, raw: str) -> None:
        try:
            data = json.loads(raw)
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


# ── Main ──────────────────────────────────────────────────────────────────────

class CommandWriter:
    """Best-effort UI-to-daemon command sender."""

    def __init__(self):
        self._last_verify_at = 0.0

    def verify_requested(self, source: str) -> None:
        now = time.monotonic()
        if now - self._last_verify_at < 0.35:
            return
        self._last_verify_at = now
        payload = json.dumps({"cmd": "verify_requested", "source": source}, separators=(",", ":")) + "\n"
        self._write_command(payload)

    def emergency_lock(self, source: str = "ui") -> None:
        payload = json.dumps({"cmd": "emergency_lock", "source": source}, separators=(",", ":")) + "\n"
        self._write_command(payload)

    def _write_command(self, payload: str) -> None:
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        INVALID = ctypes.c_void_p(-1).value
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
        if handle == INVALID:
            log.debug("Command pipe unavailable")
            return
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
        finally:
            k32.CloseHandle(handle)


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    command_writer = CommandWriter()
    disable_soft_lock_overlay = os.environ.get("MG_DISABLE_SOFT_LOCK_OVERLAY", "0") == "1"
    shield = None if disable_soft_lock_overlay else SoftLockOverlay(on_verify_requested=command_writer.verify_requested)
    widget = IslandWidget(
        on_verify_requested=command_writer.verify_requested,
        on_overlay_dissolve=shield.dissolve if shield is not None else None,
    )
    widget.show()
    widget.apply_state(get_state("idle"))

    sig        = StateSignal()
    stop_event = threading.Event()
    reader     = PipeReader(sig, stop_event)

    def _apply_state(state):
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
        app.quit()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Let Ctrl+C propagate through Qt event loop
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
