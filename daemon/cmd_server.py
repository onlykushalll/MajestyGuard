r"""
cmd_server.py - UI-to-daemon named pipe commands.

Pipe: \\.\pipe\MajestyGuard_CMD

The UI can request verification or emergency lock, but commands never
authorize access directly. The daemon still owns every security decision.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import pywintypes  # type: ignore
import win32file  # type: ignore
import win32pipe  # type: ignore
import win32security  # type: ignore

log = logging.getLogger(__name__)

CMD_PIPE_NAME = r"\\.\pipe\MajestyGuard_CMD"
VALID_COMMANDS = frozenset({"verify_requested", "emergency_lock", "windows_lock_used", "simulate_crash"})


def cmd_payload(cmd: str, source: str = "") -> dict:
    """Build a command payload for the UI-to-daemon pipe."""
    return {"cmd": cmd, "source": source}


def parse_cmd(raw: str) -> tuple[str, str] | None:
    """Parse a newline-delimited command message."""
    try:
        payload = json.loads(raw.strip())
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    cmd = payload.get("cmd")
    source = payload.get("source", "")
    if cmd not in VALID_COMMANDS:
        return None
    if not isinstance(source, str):
        source = ""
    return cmd, source[:80]


def _build_sa() -> win32security.SECURITY_ATTRIBUTES:
    # Define SDDL: System (SY) and Admin (BA) Full Access, Interactive (IU) Read/Write
    sddl = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;IU)"
    sd_compiled = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd_compiled
    sa.bInheritHandle = 0
    return sa


class CMDServer:
    """Daemon-side named pipe server for local UI commands."""

    def __init__(self, on_command, pipe_name: str = CMD_PIPE_NAME):
        self.pipe_name = pipe_name
        self._on_command = on_command
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mg-cmd-ipc", daemon=True)
        self._thread.start()
        log.info("CMDServer started on %s", self.pipe_name)

    def stop(self) -> None:
        self._stop.set()
        try:
            handle = win32file.CreateFile(
                self.pipe_name,
                win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            win32file.CloseHandle(handle)
        except pywintypes.error:
            pass
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("CMDServer stopped")

    def _loop(self) -> None:
        sa = _build_sa()
        while not self._stop.is_set():
            handle = None
            try:
                handle = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_INBOUND,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    4096,
                    4096,
                    0,
                    sa,
                )
                try:
                    win32pipe.ConnectNamedPipe(handle, None)
                except pywintypes.error as exc:
                    if getattr(exc, "winerror", None) != 535:
                        raise
                if self._stop.is_set():
                    break
                _hr, raw = win32file.ReadFile(handle, 4096)
                parsed = parse_cmd(raw.decode("utf-8", errors="replace"))
                if parsed is None:
                    log.warning("CMDServer ignored invalid command")
                    continue
                self._on_command(*parsed)
            except pywintypes.error as exc:
                if not self._stop.is_set():
                    log.warning("CMDServer pipe error: %s", exc)
                    time.sleep(0.2)
            except Exception:
                log.exception("CMDServer unexpected error")
                time.sleep(0.5)
            finally:
                if handle is not None:
                    try:
                        win32pipe.DisconnectNamedPipe(handle)
                    except pywintypes.error:
                        pass
                    try:
                        win32file.CloseHandle(handle)
                    except pywintypes.error:
                        pass
