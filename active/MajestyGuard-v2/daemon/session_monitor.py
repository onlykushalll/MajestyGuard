"""
Windows session event monitor for the user-space daemon.

The daemon needs to know when Windows itself has entered the secure lock screen
so it can stop camera processing without trying to second-guess Secure Desktop.
This module is intentionally lazy about pywin32 imports so unit tests and
non-Windows tooling can import it without side effects.
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional


log = logging.getLogger("majestyguard.session")

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOGON = 0x5
WTS_SESSION_LOGOFF = 0x6
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8


class SessionEvent(Enum):
    SESSION_LOGON = "session_logon"
    SESSION_LOGOFF = "session_logoff"
    SESSION_LOCK = "session_lock"
    SESSION_UNLOCK = "session_unlock"


_WTS_EVENT_MAP = {
    WTS_SESSION_LOGON: SessionEvent.SESSION_LOGON,
    WTS_SESSION_LOGOFF: SessionEvent.SESSION_LOGOFF,
    WTS_SESSION_LOCK: SessionEvent.SESSION_LOCK,
    WTS_SESSION_UNLOCK: SessionEvent.SESSION_UNLOCK,
}


def decode_wts_session_event(wparam: int) -> Optional[SessionEvent]:
    return _WTS_EVENT_MAP.get(int(wparam))


class SessionMonitor:
    def __init__(self, callback: Callable[[SessionEvent], None]):
        self._callback = callback
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self.available = False

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self.available
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="MajestyGuardSessionMonitor",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        hwnd = self._hwnd
        if hwnd:
            try:
                import win32gui

                win32gui.PostMessage(hwnd, 0x0010, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            import win32con
            import win32gui
            import win32ts
        except Exception as exc:
            self.available = False
            log.warning("Windows session monitor unavailable: %s", exc)
            return

        class_name = "MajestyGuardSessionMonitorWindow"
        atom = None
        try:
            wnd_class = win32gui.WNDCLASS()
            wnd_class.lpfnWndProc = self._wnd_proc
            wnd_class.lpszClassName = class_name
            atom = win32gui.RegisterClass(wnd_class)
            self._hwnd = win32gui.CreateWindow(
                atom,
                class_name,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                None,
            )
            win32ts.WTSRegisterSessionNotification(
                self._hwnd,
                win32ts.NOTIFY_FOR_THIS_SESSION,
            )
            self.available = True
            log.info("Windows session monitor started")
            while not self._stop.is_set():
                win32gui.PumpWaitingMessages()
                time.sleep(0.1)
        except Exception as exc:
            self.available = False
            log.warning("Windows session monitor stopped unexpectedly: %s", exc)
        finally:
            if self._hwnd:
                try:
                    win32ts.WTSUnRegisterSessionNotification(self._hwnd)
                except Exception:
                    pass
                try:
                    win32gui.DestroyWindow(self._hwnd)
                except Exception:
                    pass
                self._hwnd = None
            if atom:
                try:
                    win32gui.UnregisterClass(atom, None)
                except Exception:
                    pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            event = decode_wts_session_event(wparam)
            if event is not None:
                log.info("Windows session event: %s", event.value)
                self._callback(event)
            return 0
        try:
            import win32gui

            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
        except Exception:
            return 0
