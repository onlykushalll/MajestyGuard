"""Keep the lock overlay on whichever virtual desktop is current.

A window lives on one virtual desktop. If the user switches desktops (Win+Ctrl+
Left/Right, Task View, or a touchpad gesture) the overlay stays behind on the
old one and the new desktop is fully usable. IVirtualDesktopManager (documented)
lets a process move its own windows between desktops, so while locked the
overlay is moved onto the desktop the user is looking at.

Everything here fails soft: if COM cannot be set up, ``DesktopFollower.create``
returns None and the lock simply works as before.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Iterable, Optional

_CLSID_VDM = "{AA509086-5CA9-4C25-8F95-589D3C07B48A}"
_IID_VDM = "{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"
_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class ComDesktopManager:
    """Thin ctypes vtable wrapper around IVirtualDesktopManager (slots 3, 4, 5)."""

    def __init__(self) -> None:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        clsid, iid = _GUID(), _GUID()
        ole32.CLSIDFromString(_CLSID_VDM, ctypes.byref(clsid))
        ole32.CLSIDFromString(_IID_VDM, ctypes.byref(iid))
        self._ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(self._ptr))
        if hr != 0 or not self._ptr.value:
            raise OSError(f"IVirtualDesktopManager unavailable (hr={hr & 0xFFFFFFFF:#x})")
        vtbl = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE
        self._is_on = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(wintypes.BOOL))(vtbl[3])
        self._get_id = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(_GUID))(vtbl[4])
        self._move = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(_GUID))(vtbl[5])

    def is_on_current(self, hwnd: int) -> Optional[bool]:
        flag = wintypes.BOOL(0)
        try:
            self._is_on(self._ptr, hwnd, ctypes.byref(flag))
            return bool(flag.value)
        except OSError:
            return None

    def desktop_id_of(self, hwnd: int):
        guid = _GUID()
        try:
            self._get_id(self._ptr, hwnd, ctypes.byref(guid))
        except OSError:
            return None
        return guid

    def move(self, hwnd: int, desktop_id) -> bool:
        try:
            self._move(self._ptr, hwnd, ctypes.byref(desktop_id))
            return True
        except OSError:
            return False


def candidate_windows() -> list:
    """Windows that are certainly on the desktop the user is looking at."""
    user32 = ctypes.windll.user32
    found = [user32.GetForegroundWindow(), user32.FindWindowW("Shell_TrayWnd", None), user32.GetShellWindow()]
    return [int(h) for h in found if h]


class DesktopFollower:
    def __init__(self, manager, candidates: Callable[[], Iterable[int]] = candidate_windows) -> None:
        self._m = manager
        self._candidates = candidates

    @classmethod
    def create(cls) -> "Optional[DesktopFollower]":
        try:
            return cls(ComDesktopManager())
        except Exception:
            return None

    def follow(self, overlay_hwnd: int) -> bool:
        """Move the overlay to the current desktop if it is elsewhere.

        Returns True only when a move was performed.
        """
        if not overlay_hwnd:
            return False
        on_current = self._m.is_on_current(overlay_hwnd)
        if on_current is not False:               # True, or unknown -> do nothing
            return False
        for hwnd in self._candidates():
            if hwnd == overlay_hwnd:
                continue
            desktop = self._m.desktop_id_of(hwnd)
            if desktop is not None and self._m.move(overlay_hwnd, desktop):
                return True
        return False
