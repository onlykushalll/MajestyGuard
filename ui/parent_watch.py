"""Detect that the daemon which launched this UI process is gone.

A UI whose daemon died used to live on forever, reconnecting to the pipe (and,
once a *new* daemon started, being rejected as an unowned client in a loop).
The UI now exits when its daemon is gone -- unless the lock overlay is up, in
which case it stays so a locked desktop is never silently opened (TAB still
hands over to the Windows lock screen).

Liveness uses OpenProcess + WaitForSingleObject. Never os.kill(pid, 0): on
Windows that calls TerminateProcess and would kill the process being probed.
"""
from __future__ import annotations

import ctypes
import os
from typing import Optional

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
ENV_VAR = "MG_DAEMON_PID"


def process_alive(pid: int) -> bool:
    """True if ``pid`` is a running process. Errs on the side of 'alive' when it
    cannot tell (access denied, API failure) so the UI never exits by mistake."""
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            # 87 = ERROR_INVALID_PARAMETER: no such process. Anything else (e.g. 5 =
            # access denied) means we cannot tell, so assume it is still there.
            return k32.GetLastError() != 87
        try:
            # Only a signaled process handle proves exit. A failed probe does not.
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) != _WAIT_OBJECT_0
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        return True


def configured_daemon_pid(environ=None) -> Optional[int]:
    raw = (environ if environ is not None else os.environ).get(ENV_VAR, "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


class OrphanGuard:
    """Decide whether this UI should exit because its daemon is gone."""

    def __init__(self, daemon_pid: Optional[int], alive=process_alive, strikes_needed: int = 2) -> None:
        self.daemon_pid = daemon_pid
        self._alive = alive
        self._strikes_needed = max(1, strikes_needed)
        self._strikes = 0

    def should_exit(self, overlay_visible: bool) -> bool:
        if self.daemon_pid is None:
            return False                      # standalone UI: nothing owns it
        if self._alive(self.daemon_pid):
            self._strikes = 0
            return False
        if overlay_visible:
            return False                      # never abandon a lock by exiting
        self._strikes += 1                    # require consecutive confirmations
        return self._strikes >= self._strikes_needed


QUIT_FLAG_ENV = "MG_UI_QUIT_FLAG"


def quit_flag_path(environ=None):
    """Path of the file the daemon drops to ask this UI to shut down cleanly, or None."""
    raw = (environ if environ is not None else os.environ).get(QUIT_FLAG_ENV, "").strip()
    return raw or None


def quit_requested(path) -> bool:
    """True when the daemon asked this UI to exit (and consumes the request)."""
    if not path:
        return False
    try:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
            return True
    except OSError:
        pass
    return False
