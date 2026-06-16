"""
Windows last-input timer for MajestyGuard desktop soft-lock.

This measures keyboard/mouse inactivity only. It does not pause or restrict
background work; it simply lets the daemon decide when to raise the input shield.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def get_idle_seconds() -> float:
    """Return seconds since the last local keyboard or mouse input."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    elapsed_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, float(elapsed_ms) / 1000.0)
