"""
Standalone, time-bounded test of mouse+keyboard lockdown.
Engages for 8 seconds, then ALWAYS releases — even on exception.
Run this in isolation before wiring lockdown into the real daemon.

Usage:
    python daemon/test_input_lockdown_standalone.py

Manual checks during the 8-second window:
  1. Mouse clicks do nothing, cursor cannot move
  2. Tab and Space print to console, every other key does nothing
  3. Press Ctrl+Alt+Del — secure desktop appears normally
  4. From Task Manager, kill this script — mouse must recover
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import threading
import time

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

VK_TAB = 0x09
VK_SPACE = 0x20
VK_CTRL = 0x11
VK_ALT = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_BLOCKED_MOUSE_MSGS = {
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP,
    WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEWHEEL, WM_MOUSEHWHEEL,
    WM_XBUTTONDOWN, WM_XBUTTONUP,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_kb_hook = None
_mouse_hook = None
_locked = True


def _any_modifier_held() -> bool:
    for vk in (VK_CTRL, VK_ALT, VK_LWIN, VK_RWIN):
        if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def _kb_proc(nCode, wParam, lParam):
    if nCode >= 0 and _locked:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if kb.vkCode in (VK_TAB, VK_SPACE) and not _any_modifier_held():
                name = "TAB" if kb.vkCode == VK_TAB else "SPACE"
                print(f"  [ALLOWED] {name} pressed")
                return ctypes.windll.user32.CallNextHookEx(_kb_hook, nCode, wParam, lParam)
            return 1
        return 1
    return ctypes.windll.user32.CallNextHookEx(_kb_hook, nCode, wParam, lParam)


def _mouse_proc(nCode, wParam, lParam):
    if nCode >= 0 and _locked and wParam in _BLOCKED_MOUSE_MSGS:
        return 1
    return ctypes.windll.user32.CallNextHookEx(_mouse_hook, nCode, wParam, lParam)


def _engage_cursor_lock():
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    cx, cy = w // 2, h // 2
    rect = wintypes.RECT(cx, cy, cx + 1, cy + 1)
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))


def _release_cursor_lock():
    ctypes.windll.user32.ClipCursor(None)


def _release_all():
    global _locked, _kb_hook, _mouse_hook
    _locked = False
    _release_cursor_lock()
    if _kb_hook:
        ctypes.windll.user32.UnhookWindowsHookEx(_kb_hook)
        _kb_hook = None
    if _mouse_hook:
        ctypes.windll.user32.UnhookWindowsHookEx(_mouse_hook)
        _mouse_hook = None
    print("\n[RELEASED] All hooks and cursor lock released.")


def main():
    global _kb_hook, _mouse_hook

    import atexit
    atexit.register(_release_all)

    DURATION = 8
    print(f"=== Input Lockdown Test: engaging for {DURATION} seconds ===")
    print("During this window:")
    print("  - Mouse clicks should do nothing, cursor pinned")
    print("  - TAB and SPACE should print here")
    print("  - All other keys should be blocked")
    print("  - Ctrl+Alt+Del should still work (secure desktop)")
    print()

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p)
    kb_ref = HOOKPROC(_kb_proc)
    mouse_ref = HOOKPROC(_mouse_proc)

    _kb_hook = ctypes.windll.user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb_ref, None, 0)
    _mouse_hook = ctypes.windll.user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_ref, None, 0)
    _engage_cursor_lock()

    print(f"[ENGAGED] Hooks installed. Releasing in {DURATION}s...")

    end_time = time.monotonic() + DURATION
    msg = ctypes.wintypes.MSG()
    try:
        while time.monotonic() < end_time:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                break
            if ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            else:
                _engage_cursor_lock()
                time.sleep(0.01)
    finally:
        _release_all()

    print("\n=== Test complete. Verify: ===")
    print("  1. Mouse moves freely now")
    print("  2. All keys work normally now")
    print("  3. If you killed this via Task Manager during test, mouse recovered")


if __name__ == "__main__":
    main()
