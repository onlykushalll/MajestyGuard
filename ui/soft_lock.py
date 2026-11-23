"""
Fullscreen MajestyGuard desktop soft-lock shield.

This is the production desktop lock surface used before the signed Windows
Credential Provider path is available. It blocks local input with a full-screen
glass shield while background apps keep running.
"""
from __future__ import annotations

import math
import ctypes
import atexit
import time

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PyQt6.QtWidgets import QApplication, QWidget

from states import IslandState, get_state
import lock_layout as _layout
import tokens as _tok
from tab_confirm import ACTION_LOCK, TabConfirmGate

try:  # liquid-glass lenses are optional: any failure keeps the flat paint
    import glass as _glass
except Exception:  # pragma: no cover
    _glass = None

try:
    from fonts import ui_font_px as _ui_font
except Exception:  # pragma: no cover
    _ui_font = None


def _font(size, weight=QFont.Weight.Normal, **kw) -> QFont:
    """Segoe UI Variable via fonts.ui_font; plain Segoe UI if that is unavailable."""
    if _ui_font is not None:
        try:
            return _ui_font(size, weight, **kw)
        except Exception:
            pass
    fallback = QFont("Segoe UI")
    fallback.setPixelSize(max(6, int(round(float(size) * 96.0 / 72.0))))
    fallback.setWeight(weight)
    return fallback


_LOCK_NAMES = {
    "locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock",
    "verify_failed", "camera_fault",
}

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
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
VK_SHIFT = 0x10
VK_CTRL = 0x11
VK_ALT = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_MEDIA_STOP = 0xB2
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001

import ctypes.wintypes as wintypes

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

ctypes.windll.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
ctypes.windll.user32.SetWindowsHookExW.restype = HHOOK

ctypes.windll.user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, WPARAM, LPARAM]
ctypes.windll.user32.CallNextHookEx.restype = LRESULT

ctypes.windll.user32.UnhookWindowsHookEx.argtypes = [HHOOK]
ctypes.windll.user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_BLOCKED_MOUSE_MSGS = {
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP,
    WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEWHEEL, WM_MOUSEHWHEEL,
    WM_XBUTTONDOWN, WM_XBUTTONUP,
}


_kb_hook_id = None
_mouse_hook_id = None
_kb_callback_ref = None
_mouse_callback_ref = None
_hook_thread = None
_hook_thread_stop = False
_overlay_locked = False
_mouse_locked = False
_hooks_ready = None
# The SoftLockOverlay instance currently locked. Set by _install_hooks(),
# cleared by _uninstall_hooks(). Lets the hook thread safely signal Space/
# Tab back to the main Qt thread instead of forwarding them via
# CallNextHookEx, which would let them reach whatever window (including a
# background app) currently has OS focus, not just this overlay.
_active_overlay = None


def _any_modifier_held() -> bool:
    for vk in (VK_SHIFT, VK_CTRL, VK_ALT, VK_LWIN, VK_RWIN):
        if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def _keyboard_ll_callback(nCode, wParam, lParam):
    if nCode >= 0 and _overlay_locked:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if kb.vkCode in (VK_TAB, VK_SPACE) and not _any_modifier_held():
                # Consume here rather than forwarding via CallNextHookEx --
                # forwarding would let the key reach whatever window
                # currently has OS focus, which could be a background app
                # (confirmed: this leaked Space to a background YouTube tab,
                # triggering play/pause, while locked). Signal the main Qt
                # thread instead so the overlay's own verify/lock action
                # still fires, safely across threads via a queued signal.
                overlay = _active_overlay
                if overlay is not None:
                    if kb.vkCode == VK_SPACE:
                        overlay.hook_space_pressed.emit()
                    else:
                        overlay.hook_tab_pressed.emit()
            return 1
        # WM_KEYUP / WM_SYSKEYUP: always pass through. Releasing a key cannot
        # trigger any bypass action, and swallowing it leaves the OS thinking
        # the key is still held — only KEYDOWN needs to be gated.
        return ctypes.windll.user32.CallNextHookEx(_kb_hook_id, nCode, wParam, lParam)
    return ctypes.windll.user32.CallNextHookEx(_kb_hook_id, nCode, wParam, lParam)


def _mouse_ll_callback(nCode, wParam, lParam):
    if nCode >= 0 and _mouse_locked and wParam in _BLOCKED_MOUSE_MSGS:
        return 1
    return ctypes.windll.user32.CallNextHookEx(_mouse_hook_id, nCode, wParam, lParam)


def _engage_cursor_lock():
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    cx, cy = screen_w // 2, screen_h // 2
    try:
        ctypes.windll.user32.SetCursorPos(cx, cy)
    except Exception:
        pass
    rect = wintypes.RECT(cx, cy, cx + 1, cy + 1)
    if not ctypes.windll.user32.ClipCursor(ctypes.byref(rect)):
        import logging
        err = ctypes.windll.kernel32.GetLastError()
        logging.getLogger("MajestyGuard.UI").error(
            "ClipCursor FAILED (GetLastError=%d) - mouse position will NOT "
            "be pinned while locked.", err,
        )


def _release_cursor_lock():
    ctypes.windll.user32.ClipCursor(None)


def _stop_background_media():
    """Send a one-way media stop signal when a privacy lock engages.

    Some browser players do not implement a distinct stop action, but a
    play/pause toggle is unsafe here because it can start previously paused
    media. The lock must never create background playback as a side effect.
    """
    try:
        ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, 2, 0)  # KEYEVENTF_KEYUP
    except Exception:
        import logging
        logging.getLogger("MajestyGuard.UI").warning(
            "Failed to send media-stop keys on lock engage", exc_info=True,
        )


# --- Accessibility-shortcut lockdown (Sticky/Toggle/Filter Keys) ---
# These are handled by Windows below the WH_KEYBOARD_LL hook chain (5x-Shift,
# holding Shift 8s, holding NumLock 5s), so no keyboard hook can intercept
# them — they can pop an OS-level dialog regardless of anything this app
# does. This is Microsoft's own documented kiosk-mode mitigation: clear only
# the HOTKEYACTIVE bit while locked (the shortcut trigger), leave every other
# setting untouched, and restore the original saved value on unlock.
SPI_GETFILTERKEYS, SPI_SETFILTERKEYS = 0x0032, 0x0033
SPI_GETTOGGLEKEYS, SPI_SETTOGGLEKEYS = 0x0034, 0x0035
SPI_GETSTICKYKEYS, SPI_SETSTICKYKEYS = 0x003A, 0x003B
_SPIF_SENDCHANGE = 0x0002
_HOTKEYACTIVE_BIT = 0x00000004  # same bit position in all three structs


class _STICKYKEYS(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint32)]


class _TOGGLEKEYS(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint32)]


class _FILTERKEYS(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint32),
        ("iWaitMSec", ctypes.c_uint32), ("iDelayMSec", ctypes.c_uint32),
        ("iRepeatMSec", ctypes.c_uint32), ("iBounceMSec", ctypes.c_uint32),
    ]


_saved_stickykeys: "_STICKYKEYS | None" = None
_saved_togglekeys: "_TOGGLEKEYS | None" = None
_saved_filterkeys: "_FILTERKEYS | None" = None


def _disable_accessibility_shortcuts() -> None:
    global _saved_stickykeys, _saved_togglekeys, _saved_filterkeys
    user32 = ctypes.windll.user32
    try:
        # Save the user's ORIGINAL state only once per lock. A repeat call
        # (lock re-engaged while hooks are still up) would otherwise read the
        # already-disabled values and save those, so unlock could never
        # restore the real settings.
        sk = _STICKYKEYS(cbSize=ctypes.sizeof(_STICKYKEYS))
        user32.SystemParametersInfoW(SPI_GETSTICKYKEYS, ctypes.sizeof(sk), ctypes.byref(sk), 0)
        if _saved_stickykeys is None:
            _saved_stickykeys = _STICKYKEYS(cbSize=sk.cbSize, dwFlags=sk.dwFlags)
        sk.dwFlags &= ~_HOTKEYACTIVE_BIT
        user32.SystemParametersInfoW(SPI_SETSTICKYKEYS, ctypes.sizeof(sk), ctypes.byref(sk), _SPIF_SENDCHANGE)

        tk = _TOGGLEKEYS(cbSize=ctypes.sizeof(_TOGGLEKEYS))
        user32.SystemParametersInfoW(SPI_GETTOGGLEKEYS, ctypes.sizeof(tk), ctypes.byref(tk), 0)
        if _saved_togglekeys is None:
            _saved_togglekeys = _TOGGLEKEYS(cbSize=tk.cbSize, dwFlags=tk.dwFlags)
        tk.dwFlags &= ~_HOTKEYACTIVE_BIT
        user32.SystemParametersInfoW(SPI_SETTOGGLEKEYS, ctypes.sizeof(tk), ctypes.byref(tk), _SPIF_SENDCHANGE)

        fk = _FILTERKEYS(cbSize=ctypes.sizeof(_FILTERKEYS))
        user32.SystemParametersInfoW(SPI_GETFILTERKEYS, ctypes.sizeof(fk), ctypes.byref(fk), 0)
        if _saved_filterkeys is None:
            _saved_filterkeys = _FILTERKEYS(
                cbSize=fk.cbSize, dwFlags=fk.dwFlags, iWaitMSec=fk.iWaitMSec,
                iDelayMSec=fk.iDelayMSec, iRepeatMSec=fk.iRepeatMSec, iBounceMSec=fk.iBounceMSec,
            )
        fk.dwFlags &= ~_HOTKEYACTIVE_BIT
        user32.SystemParametersInfoW(SPI_SETFILTERKEYS, ctypes.sizeof(fk), ctypes.byref(fk), _SPIF_SENDCHANGE)
    except Exception:
        import logging
        logging.getLogger("MajestyGuard.UI").warning(
            "Disabling accessibility-shortcut hotkeys (Sticky/Toggle/Filter "
            "Keys) failed - proceeding with the lock anyway since the "
            "primary lockdown (overlay, cursor clip, input hooks) does not "
            "depend on this.", exc_info=True,
        )
        # Still never let this block the actual lock from engaging.


def _restore_accessibility_shortcuts() -> None:
    global _saved_stickykeys, _saved_togglekeys, _saved_filterkeys
    user32 = ctypes.windll.user32
    try:
        if _saved_stickykeys is not None:
            user32.SystemParametersInfoW(SPI_SETSTICKYKEYS, ctypes.sizeof(_saved_stickykeys), ctypes.byref(_saved_stickykeys), _SPIF_SENDCHANGE)
        if _saved_togglekeys is not None:
            user32.SystemParametersInfoW(SPI_SETTOGGLEKEYS, ctypes.sizeof(_saved_togglekeys), ctypes.byref(_saved_togglekeys), _SPIF_SENDCHANGE)
        if _saved_filterkeys is not None:
            user32.SystemParametersInfoW(SPI_SETFILTERKEYS, ctypes.sizeof(_saved_filterkeys), ctypes.byref(_saved_filterkeys), _SPIF_SENDCHANGE)
    except Exception:
        import logging
        logging.getLogger("MajestyGuard.UI").warning(
            "Restoring accessibility-shortcut hotkeys after unlock failed - "
            "Sticky/Toggle/Filter Keys shortcuts may remain suppressed "
            "until next login or a manual toggle in Windows Settings.",
            exc_info=True,
        )
    finally:
        _saved_stickykeys = _saved_togglekeys = _saved_filterkeys = None


def _hook_thread_func():
    global _kb_hook_id, _kb_callback_ref, _mouse_hook_id, _mouse_callback_ref, _hook_thread_stop
    import time as _time
    import logging
    log = logging.getLogger("MajestyGuard.UI")
    _kb_callback_ref = HOOKPROC(_keyboard_ll_callback)
    _kb_hook_id = ctypes.windll.user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, _kb_callback_ref, None, 0
    )

    if not _kb_hook_id:
        _kb_err = ctypes.windll.kernel32.GetLastError()
        log.error(
            "Keyboard hook install FAILED - SetWindowsHookExW(WH_KEYBOARD_LL) "
            "returned NULL (GetLastError=%d). Keyboard input will NOT be "
            "blocked while locked.", _kb_err,
        )

    _mouse_callback_ref = HOOKPROC(_mouse_ll_callback)
    _mouse_hook_id = ctypes.windll.user32.SetWindowsHookExW(
        WH_MOUSE_LL, _mouse_callback_ref, None, 0
    )
    if not _mouse_hook_id:
        _mouse_err = ctypes.windll.kernel32.GetLastError()
        log.error(
            "Mouse hook install FAILED - SetWindowsHookExW(WH_MOUSE_LL) "
            "returned NULL (GetLastError=%d). Mouse-click blocking will NOT "
            "be active (cursor-position clamp via ClipCursor is separate "
            "and unaffected).", _mouse_err,
        )

    if _hooks_ready is not None:
        _hooks_ready.set()

    msg = ctypes.wintypes.MSG()
    follower = None
    try:
        from vdesktop_guard import DesktopFollower
        follower = DesktopFollower.create()
    except Exception:
        follower = None
    _last_desktop_check = 0.0
    _last_taskmgr_check = 0.0
    _last_hook_refresh = _time.monotonic()
    while not _hook_thread_stop:
        if ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
        else:
            _time.sleep(0.01)
        # Throttled, off-main-thread Task Manager close. Was previously a
        # synchronous FindWindowW/PostMessageW call inside the 60fps UI tick —
        # moved here so a slow window enumeration can never stall the overlay
        # animation.
        now = _time.monotonic()
        if _overlay_locked and (now - _last_taskmgr_check) >= 0.4:
            _last_taskmgr_check = now
            try:
                hwnd_taskmgr = ctypes.windll.user32.FindWindowW("TaskManagerWindow", None)
                if hwnd_taskmgr:
                    ctypes.windll.user32.PostMessageW(hwnd_taskmgr, WM_CLOSE, 0, 0)
            except Exception:
                pass

        if follower is not None and _overlay_locked and _overlay_hwnd and (now - _last_desktop_check) >= 0.2:
            _last_desktop_check = now
            try:
                follower.follow(_overlay_hwnd)
            except Exception:
                pass

        if _overlay_locked and (now - _last_hook_refresh) >= 5.0:
            _last_hook_refresh = now
            try:
                new_kb = ctypes.windll.user32.SetWindowsHookExW(
                    WH_KEYBOARD_LL, _kb_callback_ref, None, 0
                )
                if new_kb:
                    old_kb = _kb_hook_id
                    _kb_hook_id = new_kb
                    if old_kb:
                        ctypes.windll.user32.UnhookWindowsHookEx(old_kb)
                new_mouse = ctypes.windll.user32.SetWindowsHookExW(
                    WH_MOUSE_LL, _mouse_callback_ref, None, 0
                )
                if new_mouse:
                    old_mouse = _mouse_hook_id
                    _mouse_hook_id = new_mouse
                    if old_mouse:
                        ctypes.windll.user32.UnhookWindowsHookEx(old_mouse)
            except Exception:
                pass


_gesture_guard = None
_overlay_hwnd = 0


def _engage_gestures(overlay) -> None:
    """Touchpad gestures bypass the input hooks (they are shell-level), so they are
    switched off for the duration of the lock and the overlay follows desktop switches."""
    global _gesture_guard, _overlay_hwnd
    import logging
    log = logging.getLogger("MajestyGuard.UI")
    try:
        if overlay is not None:
            _overlay_hwnd = int(overlay.winId())
    except Exception:
        _overlay_hwnd = 0
    try:
        if _gesture_guard is None:
            from gesture_guard import GestureGuard
            _gesture_guard = GestureGuard()
        if not _gesture_guard.engage():
            log.warning("Touchpad gestures could not be disabled; Task View gestures may work while locked")
    except Exception:
        log.warning("Touchpad gesture lock failed", exc_info=True)


def _restore_gestures() -> None:
    global _overlay_hwnd
    _overlay_hwnd = 0
    try:
        if _gesture_guard is not None:
            _gesture_guard.restore()
    except Exception:
        import logging
        logging.getLogger("MajestyGuard.UI").warning("Restoring touchpad gestures failed", exc_info=True)


def recover_gestures_after_crash() -> None:
    """UI start-up: undo a gesture lock left behind by a crashed previous lock."""
    try:
        from gesture_guard import GestureGuard
        GestureGuard().recover_after_crash()
    except Exception:
        pass


def _install_hooks(overlay=None) -> None:
    global _overlay_locked, _mouse_locked, _hook_thread, _hook_thread_stop, _hooks_ready, _active_overlay
    _overlay_locked = True
    _mouse_locked = True
    _active_overlay = overlay
    _engage_cursor_lock()
    _disable_accessibility_shortcuts()
    _engage_gestures(overlay)
    if _kb_hook_id is not None:
        return
    _hook_thread_stop = False
    import threading
    import logging
    _hooks_ready = threading.Event()
    _hook_thread = threading.Thread(target=_hook_thread_func, name="mg-input-hook", daemon=True)
    _hook_thread.start()
    # Block briefly until the hook thread has actually attempted
    # installation and reported success/failure. Previously this function
    # returned the instant the thread was merely *scheduled* to run, not
    # once hooks were actually active -- leaving a real, OS-scheduling-
    # dependent race window where ClipCursor (synchronous, above) had
    # already engaged but the keyboard/mouse hooks had not, so keystrokes
    # or clicks could pass through unblocked for however long the OS took
    # to actually run the new thread. 1s is far more than SetWindowsHookExW
    # itself ever needs; hitting this timeout means something is wrong and
    # should be visible, not silent.
    if not _hooks_ready.wait(timeout=1.0):
        logging.getLogger("MajestyGuard.UI").error(
            "Input hook thread did not confirm installation within 1s - "
            "keyboard/mouse-click blocking may not be active yet. Overlay "
            "display and cursor-position lock remain engaged regardless."
        )


def _uninstall_hooks() -> None:
    global _kb_hook_id, _kb_callback_ref, _mouse_hook_id, _mouse_callback_ref
    global _overlay_locked, _mouse_locked, _hook_thread_stop, _hook_thread, _active_overlay
    _overlay_locked = False
    _mouse_locked = False
    _active_overlay = None
    _release_cursor_lock()
    _restore_accessibility_shortcuts()
    _restore_gestures()
    _hook_thread_stop = True

    # Unhook Windows hook handles first while Python callback references remain strongly held
    if _kb_hook_id is not None:
        try:
            ctypes.windll.user32.UnhookWindowsHookEx(_kb_hook_id)
        except Exception:
            pass
        _kb_hook_id = None
    if _mouse_hook_id is not None:
        try:
            ctypes.windll.user32.UnhookWindowsHookEx(_mouse_hook_id)
        except Exception:
            pass
        _mouse_hook_id = None

    # Join hook message pump thread to guarantee no incoming events are processed
    t = _hook_thread
    if t is not None and t.is_alive():
        try:
            t.join(timeout=1.0)
        except Exception:
            pass
    _hook_thread = None

    # Safely release GC references to callback wrappers only after thread is dead
    _kb_callback_ref = None
    _mouse_callback_ref = None



# Legacy aliases for backward compat with tests
_install_keyboard_hook = _install_hooks
_uninstall_keyboard_hook = _uninstall_hooks


def _set_taskbar_visible(visible: bool) -> None:
    """Show/hide the taskbar. Defense-in-depth alongside the fullscreen overlay —
    the overlay already covers it, but actually hiding it prevents any edge-case
    flash (e.g. during opacity animation) and matches what the function name
    promises, rather than being a silent no-op."""
    try:
        user32 = ctypes.windll.user32
        cmd = 5 if visible else 0  # SW_SHOW / SW_HIDE
        for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = user32.FindWindowW(cls, None)
            if hwnd:
                user32.ShowWindow(hwnd, cmd)
    except Exception:
        pass


def _atexit_release_all():
    _uninstall_hooks()
    _set_taskbar_visible(True)


atexit.register(_atexit_release_all)


class SoftLockOverlay(QWidget):
    """Fullscreen, topmost, frameless glass shield for desktop soft-lock."""
    background_ready = pyqtSignal(QImage, int)
    lens_source_ready = pyqtSignal(object, int)
    hook_space_pressed = pyqtSignal()
    hook_tab_pressed = pyqtSignal()

    def __init__(self, on_verify_requested=None, on_windows_lock_used=None):
        super().__init__()
        self.background_ready.connect(self._on_background_ready)
        self.lens_source_ready.connect(self._on_lens_source_ready)
        self._lens_src = None          # BGR uint8, half-res, logical px * 0.5
        self._lens_cache: dict = {}
        self._tick_count = 0
        self.hook_space_pressed.connect(lambda: self._request_verification("overlay_key"))
        # Tab hands over to Windows at once while the camera works; during a
        # camera fault it needs a deliberate second press (see tab_confirm).
        self._tab_gate = TabConfirmGate()
        self.hook_tab_pressed.connect(self._on_tab_pressed)
        self._state: IslandState = get_state("idle")
        self._on_verify_requested = on_verify_requested
        self._on_windows_lock_used = on_windows_lock_used
        self._background = QPixmap()
        self._noise_image = self._build_noise_texture()
        self._noise = QPixmap.fromImage(self._noise_image)
        self._phase = 0.0
        self._opacity_value = 0.0
        self._allow_close = False
        self._lock_shown_at = None
        self._lock_generation = 0
        self._dissolve_generation = -1
        self._last_win32_reassert_at = 0.0

        # The mouse is pinned while locked, so there are no buttons: the SPACE and
        # TAB key caps are painted and driven by the keyboard hook alone.
        self._key_flash: dict = {}
        self._state_changed_at = time.monotonic()
        self._layout_cache = None

        self._setup_window()
        self._setup_motion()
        self._fit_virtual_screen()

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screen_changed)
            app.screenRemoved.connect(self._on_screen_changed)
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                primary.geometryChanged.connect(self._on_screen_changed)
                primary.virtualGeometryChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, *_args) -> None:
        self._fit_virtual_screen()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _setup_motion(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self._fade = QPropertyAnimation(self, b"overlayOpacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._dissolve_anim = QPropertyAnimation(self, b"overlayOpacity", self)
        self._dissolve_anim.setDuration(600)
        self._dissolve_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _fit_virtual_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.virtualGeometry()
        self.setGeometry(rect)
        self.setMinimumSize(rect.size())

    def _hud_rect(self) -> QRect:
        """Return primary-screen bounds in local widget coordinates so HUD elements
        never split across a multi-monitor bezel while the glass spans virtualGeometry."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return self.rect()
        vrect = screen.virtualGeometry()
        prect = screen.geometry()
        if vrect.width() <= 0 or vrect.height() <= 0:
            return self.rect()
        return QRect(
            prect.x() - vrect.x(),
            prect.y() - vrect.y(),
            prect.width(),
            prect.height(),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_cache = None

    def _force_topmost(self) -> None:
        try:
            import ctypes

            ctypes.windll.user32.SetWindowPos(
                int(self.winId()),
                -1,  # HWND_TOPMOST
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0010 | 0x0040,
            )
        except Exception:
            pass

    def _set_capture_protection(self, enabled: bool) -> None:
        """Apply WDA_MONITOR while locked so external screen-capture / screen-share
        tools cannot record the desktop or lock overlay."""
        try:
            affinity = WDA_MONITOR if enabled else WDA_NONE
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), affinity)
        except Exception:
            pass

    def apply_state(self, state: IslandState) -> None:
        if state.name != self._state.name:
            self._tab_gate.reset()
        if state.name != self._state.name:
            self._state_changed_at = time.monotonic()
        if state.name == self._state.name and self.isVisible():
            self._state = state
            # Only a lock-family repeat re-asserts lock ownership. The daemon
            # broadcasts "active" on every ACTIVE frame, and those repeats land
            # while the 600ms unlock dissolve is still running; treating them
            # as a lock refresh cancelled the dissolve and reinstalled the
            # input hooks, re-locking the desktop after a successful unlock.
            if state.name in _LOCK_NAMES:
                self._resume_lock_state(new_lock=False)
                self._force_topmost()
                self.raise_()
            return
        self._state = state
        if state.name in _LOCK_NAMES:
            entering_lock = not self.isVisible()
            self._resume_lock_state(new_lock=entering_lock)
            if entering_lock:
                self._opacity_value = 0.0  # Reset opacity before showing to avoid one-frame flash
                self._fit_virtual_screen()
                # Stutter-on-load fix: show first, capture second. grabWindow()
                # is a slow, blocking OS call — running it before showFullScreen()
                # froze the desktop for its duration, then the overlay "popped in"
                # all at once. Now the window (and whatever backdrop is already
                # cached from a previous lock cycle, or the paintEvent fallback
                # fill on the very first lock) appears immediately, and the fresh
                # screenshot capture is deferred one event-loop tick so it runs
                # after the window has already painted and become visible.
                self.showFullScreen()
                self._force_topmost()
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                self._timer.start()
                self._animate_opacity(0.0, 1.0)
                QTimer.singleShot(0, self._capture_background)

                self._lock_shown_at = time.monotonic()
                self._state_changed_at = self._lock_shown_at
            else:
                self._force_topmost()
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.update()
            return

        if state.name not in _LOCK_NAMES:
            if self.isVisible():
                self.dissolve()
            else:
                _uninstall_hooks()
                _set_taskbar_visible(True)

    def _resume_lock_state(self, *, new_lock: bool) -> None:
        """Cancel an obsolete unlock animation and restore lock ownership."""
        self._lock_generation += 1
        if self._dissolve_anim.state() != QAbstractAnimation.State.Stopped:
            self._dissolve_anim.stop()
            self._animate_opacity(self._opacity_value, 1.0)
        self._timer.start()
        _install_hooks(self)
        _set_taskbar_visible(False)
        if new_lock:
            _stop_background_media()

    @staticmethod
    def _ambient_image(w: int, h: int) -> QImage:
        """Content-free lock backdrop (generated pastel field; solid fallback)."""
        w, h = max(16, int(w)), max(16, int(h))
        try:
            if _glass is not None and _glass.glass_available():
                arr = _glass.build_ambient(w, h)
                return QImage(arr.tobytes(), w, h, w * 3, QImage.Format.Format_BGR888).copy()
        except Exception:
            pass
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(233, 237, 243))
        return img

    def _capture_background(self) -> None:
        capture_generation = self._lock_generation
        self._lens_src = None          # never refract a previous lock's desktop
        self._lens_cache.clear()
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self._background = QPixmap()
            return
        rect = screen.virtualGeometry()
        # Privacy: the real screen is never read. The lock backdrop is a
        # generated light field, so nothing of the desktop behind it (colours,
        # windows, layout) can show through. Apps keep running underneath.
        shot_image = self._ambient_image(rect.width(), rect.height())
        noise_image = self._noise_image
        self._background = QPixmap()

        def _worker():
            try:
                lens_src = None
                if _glass is not None and _glass.glass_enabled():
                    try:
                        # What the glass lenses refract: the generated field at half
                        # resolution, lightly softened so rims stay crisp.
                        half = shot_image.scaled(
                            max(1, rect.width() // 2),
                            max(1, rect.height() // 2),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        bgr = _glass.qimage_to_bgr(half)
                        bgr = _glass.cv2.GaussianBlur(bgr, (0, 0), 1.1)
                        lens_src = _glass.np.clip(
                            bgr.astype(_glass.np.float32) * 0.96 + 8.0, 0.0, 255.0
                        ).astype(_glass.np.uint8)
                    except Exception as lens_exc:
                        import logging
                        logging.getLogger("MajestyGuard.UI").warning("Glass backdrop unavailable: %s", lens_exc)
                        lens_src = None

                # Static layer: the generated field, a light veil for text
                # contrast, a top highlight, a bottom shade and film grain.
                cache = QImage(shot_image.size(), QImage.Format.Format_ARGB32)
                cache.fill(Qt.GlobalColor.transparent)
                painter = QPainter(cache)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.drawImage(0, 0, shot_image)
                r = QRect(0, 0, cache.width(), cache.height())
                painter.fillRect(r, QColor(248, 250, 255, 30))
                top = QLinearGradient(0.0, 0.0, 0.0, float(r.height()))
                top.setColorAt(0.0, QColor(255, 255, 255, 84))
                top.setColorAt(0.20, QColor(255, 255, 255, 0))
                top.setColorAt(0.80, QColor(40, 50, 80, 0))
                top.setColorAt(1.0, QColor(40, 50, 80, 38))
                painter.fillRect(r, top)
                painter.fillRect(QRectF(0.0, 0.0, float(r.width()), 1.5), QColor(255, 255, 255, 150))
                if noise_image is not None and not noise_image.isNull():
                    painter.save()
                    painter.setOpacity(0.08)
                    painter.fillRect(r, QBrush(noise_image))
                    painter.restore()
                painter.end()
                if lens_src is not None:
                    self.lens_source_ready.emit(lens_src, capture_generation)
                self.background_ready.emit(cache, capture_generation)
            except Exception as e:
                import logging
                logging.getLogger("MajestyGuard.UI").error("Asynchronous background render failed: %s", e)

        import threading
        threading.Thread(target=_worker, name="mg-bg-blur", daemon=True).start()

    def _on_background_ready(self, image: QImage, capture_generation: int) -> None:
        # Rendering happens off-thread. Never let a delayed lock backdrop
        # repaint the window after its owner has already been verified.
        if (
            capture_generation != self._lock_generation
            or self._state.name not in _LOCK_NAMES
            or self._dissolve_anim.state() != QAbstractAnimation.State.Stopped
        ):
            return
        self._background = QPixmap.fromImage(image)
        self.update()

    def _on_lens_source_ready(self, src, capture_generation: int) -> None:
        if (
            capture_generation != self._lock_generation
            or self._state.name not in _LOCK_NAMES
            or self._dissolve_anim.state() != QAbstractAnimation.State.Stopped
        ):
            return
        self._lens_src = src
        self._lens_cache.clear()
        self.update()
        for child in self.findChildren(QWidget):   # the island repaints with glass
            if child.isWindow():
                child.update()

    def lens_backdrop_for(self, global_pos):
        """(backdrop, local_x, local_y) for a child window's top-left, or None
        when there is no lock backdrop to refract (not locked / not ready)."""
        if (
            self._lens_src is None
            or not self.isVisible()
            or self._state.name not in _LOCK_NAMES
        ):
            return None
        p = self.mapFromGlobal(global_pos)
        return self._lens_src, p.x(), p.y()

    def _lens_image(self, rect: QRectF, radius: float, **kw):
        """Cached refractive lens QImage for ``rect`` (integer-snapped), or None."""
        if self._lens_src is None or _glass is None or not _glass.glass_enabled():
            return None
        x, y = int(round(rect.left())), int(round(rect.top()))
        w, h = int(round(rect.width())), int(round(rect.height()))
        if w < 4 or h < 4:
            return None
        key = (x, y, w, h, round(radius, 1), tuple(sorted(kw.items())))
        img = self._lens_cache.get(key)
        if img is None:
            try:
                arr = _glass.render_lens(self._lens_src, x, y, w, h, radius, scale=0.5, **kw)
                img = _glass.bgra_to_qimage(arr)
            except Exception:
                return None
            if len(self._lens_cache) >= 12:
                self._lens_cache.clear()
            self._lens_cache[key] = img
        return img

    def _draw_lens(self, painter: QPainter, rect: QRectF, radius: float, **kw) -> bool:
        img = self._lens_image(rect, radius, **kw)
        if img is None:
            return False
        painter.drawImage(int(round(rect.left())), int(round(rect.top())), img)
        return True

    @staticmethod
    def _build_noise_texture() -> QImage:
        import numpy as np
        h = w = 192
        ys, xs = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        seed = (xs * 73856093) ^ (ys * 19349663) ^ 0xA7C15
        value = (seed ^ (seed >> 11) ^ (seed >> 23)) & 0xFF
        alpha = (1 + (value % 6)).astype(np.uint8)
        shade = np.where(value > 127, 255, 232).astype(np.uint8)
        # Build ARGB32 buffer
        argb = (alpha.astype(np.uint32) << 24) | (shade.astype(np.uint32) << 16) \
             | (shade.astype(np.uint32) << 8) | shade.astype(np.uint32)
        # Ensure QImage owns its memory using .copy()
        return QImage(argb.tobytes(), w, h, QImage.Format.Format_ARGB32).copy()

    def dissolve(self) -> None:
        # The unlock decision is already final by the time this runs. Restore
        # desktop ownership before the visual exit, then let the glass fade as
        # a purely cosmetic last step.
        self._set_capture_protection(False)
        if not self.isVisible():
            _uninstall_hooks()
            _set_taskbar_visible(True)
            return
        if self._dissolve_anim.state() != QAbstractAnimation.State.Stopped:
            return
        _uninstall_hooks()
        _set_taskbar_visible(True)
        self._dissolve_generation = self._lock_generation
        self._dissolve_anim.setStartValue(self._opacity_value)
        self._dissolve_anim.setEndValue(0.0)
        try:
            self._dissolve_anim.finished.disconnect(self._hide_after_dissolve)
        except TypeError:
            pass
        self._dissolve_anim.finished.connect(self._hide_after_dissolve)
        self._dissolve_anim.start()

    def _hide_after_dissolve(self) -> None:
        try:
            self._dissolve_anim.finished.disconnect(self._hide_after_dissolve)
        except TypeError:
            pass
        if self._dissolve_generation != self._lock_generation or self._state.name in _LOCK_NAMES:
            return
        self._timer.stop()
        self.hide()
        self._lock_shown_at = None
        self.setWindowOpacity(1.0)  # reset for next show
        self._set_capture_protection(False)
        _uninstall_hooks()
        _set_taskbar_visible(True)

    def _animate_opacity(self, start: float, end: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(start)
        self._fade.setEndValue(end)
        self._fade.start()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        now = time.monotonic()
        due_win32 = (now - getattr(self, "_last_win32_reassert_at", 0.0)) >= 0.22

        if _mouse_locked and due_win32:
            _engage_cursor_lock()

        if _overlay_locked and due_win32:
            self._last_win32_reassert_at = now
            _set_taskbar_visible(False)
            self._force_topmost()
            self.raise_()
            self.activateWindow()

        if self.isVisible():
            # Only the animated HUD regions change between frames (clock, aura,
            # scan sweep, corner pills); repainting the whole virtual desktop
            # at 30 fps was pure waste. A full repaint every second is a safety
            # net so nothing can stay stale.
            self._tick_count += 1
            if self._tick_count % 30 == 0 or self._opacity_value < 1.0:
                self.update()
            else:
                self.update(self._dynamic_region())

    def _dynamic_region(self) -> QRegion:
        region = QRegion()
        for x, y, w, h in _layout.animated_bounds(self._layout()):
            region = region.united(QRegion(QRect(int(x), int(y), int(w) + 1, int(h) + 1)))
        return region

    def _camera_fault_active(self) -> bool:
        return self._state.name == "camera_fault"

    def _on_tab_pressed(self) -> None:
        self._mark_key("TAB")
        # Windows lock is never one stray key: TAB arms, a second TAB within the window confirms.
        action = self._tab_gate.press(confirmation_required=True)
        if action == ACTION_LOCK:
            self._use_windows_lock()
        else:
            self.update()

    def _use_windows_lock(self) -> None:
        import ctypes
        import logging
        _uninstall_hooks()
        _set_taskbar_visible(True)
        try:
            locked = bool(ctypes.windll.user32.LockWorkStation())
        except Exception:
            locked = False
        if not locked:
            # Fail closed. LockWorkStation can be refused (the DisableLockWorkstation policy,
            # a session that is not interactive). Hiding the overlay anyway would hand the
            # desktop to whoever pressed TAB twice, so take the lock back instead.
            logging.getLogger("MajestyGuard.UI").error(
                "LockWorkStation failed (GetLastError=%d) - keeping the lock overlay up",
                ctypes.windll.kernel32.GetLastError(),
            )
            self._tab_gate.reset()
            self._resume_lock_state(new_lock=False)
            self._force_topmost()
            self.raise_()
            self.activateWindow()
            self.update()
            return
        # The Windows lock is engaging: only now tell the daemon and let go of the screen.
        if self._on_windows_lock_used:
            self._on_windows_lock_used()
        self.hide()
        self._lock_shown_at = None

    def getOverlayOpacity(self) -> float:
        return self._opacity_value

    def setOverlayOpacity(self, value: float) -> None:
        self._opacity_value = max(0.0, min(1.0, float(value)))
        self.update()

    overlayOpacity = pyqtProperty(float, fget=getOverlayOpacity, fset=setOverlayOpacity)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity_value)

        # Draw the fully pre-rasterized static background cache (instantaneous)
        if not self._background.isNull():
            painter.drawPixmap(self.rect(), self._background)
        else:
            painter.fillRect(self.rect(), QColor("#E9EDF3"))

        self._paint_clock(painter)
        self._paint_corner_status(painter)
        self._paint_brand_signature(painter)
        painter.end()

    # ------------------------------------------------------------------ layout
    def _layout(self):
        hud = self._hud_rect()
        key = (hud.x(), hud.y(), hud.width(), hud.height())
        cached = getattr(self, "_layout_cache", None)
        if cached is None or cached[0] != key:
            cached = (key, _layout.compute(*key))
            self._layout_cache = cached
        return cached[1]

    def _accent(self, alpha: int = 255) -> QColor:
        r, g, b = _tok.accent_for(self._state.name)
        return QColor(r, g, b, alpha)

    def _ink(self, alpha: int = 255, soft: int = 0) -> QColor:
        r, g, b = (_tok.INK, _tok.INK_SOFT, _tok.INK_FAINT)[soft]
        return QColor(r, g, b, alpha)

    def _content_alpha(self) -> float:
        """0 -> 1 over 220 ms after the state changes, so copy fades in."""
        return max(0.0, min(1.0, (time.monotonic() - self._state_changed_at) / 0.22))

    def _mark_key(self, key: str) -> None:
        self._key_flash[key] = time.monotonic()
        self.update()

    def _draw_text(self, painter, rect, text, font, color, align=Qt.AlignmentFlag.AlignHCenter):
        painter.setFont(font)
        painter.setPen(color)
        text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()))
        painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)

    # -------------------------------------------------------------------- card
    def _paint_clock(self, painter: QPainter) -> None:
        from datetime import datetime

        lay = self._layout()
        s = lay.scale
        name = self._state.name
        now = datetime.now()
        card = QRectF(*lay.card)
        cx = lay.cx
        pulse = 0.5 + 0.5 * math.sin(self._phase * math.tau * 2.0)
        painter.save()

        # State aura: a soft coloured glow behind the card in every non-idle state.
        if name not in {"locked_passive", "soft_locked"}:
            aura_r = card.width() * (0.60 + 0.03 * pulse)
            centre = QPointF(cx, card.center().y())
            aura = QRadialGradient(centre, aura_r)
            strong = int((46 + 26 * pulse) * self._content_alpha())
            aura.setColorAt(0.0, self._accent(strong))
            aura.setColorAt(0.62, self._accent(strong // 4))
            aura.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(aura)
            painter.drawEllipse(centre, aura_r, aura_r * 0.50)

        radius = 38.0 * s
        tint = (255, 255, 255)
        if name not in {"locked_passive", "soft_locked"}:
            ar, ag, ab = _tok.accent_for(name)
            tint = (255 - int((255 - ar) * 0.22), 255 - int((255 - ag) * 0.22), 255 - int((255 - ab) * 0.22))
        if not self._draw_lens(painter, card, radius, tint=tint, tint_alpha=0.40, saturation=1.10, bulge=0.06):
            path = QPainterPath()
            path.addRoundedRect(card, radius, radius)
            body = QLinearGradient(card.left(), card.top(), card.left(), card.bottom())
            body.setColorAt(0.0, QColor(255, 255, 255, 150))
            body.setColorAt(1.0, QColor(240, 244, 252, 112))
            painter.fillPath(path, body)
            painter.setPen(QPen(QColor(255, 255, 255, 190), 1.0))
            painter.drawPath(path)

        # Time, left; hairline; date + state message, right.
        time_font = _font(58 * s, QFont.Weight.Light, tabular=True, spacing=-1.6)
        self._draw_text(painter, QRectF(*lay.time), now.strftime("%H:%M"), time_font, self._ink(235),
                        Qt.AlignmentFlag.AlignHCenter)
        dx, dy, _dw, dh = lay.divider
        painter.setPen(QPen(self._ink(30), 1.0))
        painter.drawLine(QPointF(dx, dy), QPointF(dx, dy + dh))

        fade = self._content_alpha()
        self._draw_text(painter, QRectF(*lay.date), now.strftime("%A, %B %d").upper(),
                        _font(9.5 * s, QFont.Weight.DemiBold, spacing=1.4), self._ink(int(185 * fade), 1),
                        Qt.AlignmentFlag.AlignLeft)
        headline, sub = _layout.message_for(name, self._state.detail or "", self._tab_gate.armed)
        mx, my, mw, mh = lay.message
        self._draw_text(painter, QRectF(mx, my, mw, mh * 0.42), headline,
                        _font(18 * s, QFont.Weight.DemiBold), self._ink(int(240 * fade)), Qt.AlignmentFlag.AlignLeft)
        painter.setFont(_font(10 * s, QFont.Weight.Normal))
        painter.setPen(self._ink(int(176 * fade), 1))
        painter.drawText(QRectF(mx, my + mh * 0.44, mw, mh * 0.56),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), sub)

        self._paint_ruler(painter, lay, now)

        # Glass key blocks: the only actions on this screen (the mouse is pinned).
        space, tab = _layout.key_models(name, self._state.detail or "", self._tab_gate.armed)
        self._paint_key(painter, QRectF(*lay.space_key), space, s)
        self._paint_key(painter, QRectF(*lay.tab_key), tab, s)
        painter.restore()

    def _paint_ruler(self, painter: QPainter, lay, now) -> None:
        """Tick marks along the card's lower edge; the lit marker is the current second,
        the faint fill before it is the minute so far. Makes the card read as a ruler."""
        s = lay.scale
        x0, y0, w, h = lay.ruler
        step = 7.0 * s
        count = max(8, int(w // step))
        step = w / count
        frac = (now.second + now.microsecond / 1e6) / 60.0
        marker = x0 + w * frac
        base = y0 + h
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(count + 1):
            x = x0 + i * step
            major = i % 5 == 0
            near = max(0.0, 1.0 - abs(x - marker) / (46.0 * s))
            tick_h = (h * (0.78 if major else 0.44)) + h * 0.30 * near
            if x <= marker:
                col = self._accent(int(70 + 90 * near)) if near > 0.02 else self._ink(70, 1)
            else:
                col = self._ink(46 + int(60 * near), 1)
            painter.setPen(QPen(col, 1.4 if major else 1.0))
            painter.drawLine(QPointF(x, base), QPointF(x, base - tick_h))
        glow = QRadialGradient(QPointF(marker, base - h * 0.4), 16.0 * s)
        glow.setColorAt(0.0, self._accent(120))
        glow.setColorAt(1.0, self._accent(0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(marker, base - h * 0.4), 16.0 * s, 16.0 * s)
        pen = QPen(self._accent(235), 2.2 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(marker, base), QPointF(marker, base - h * 1.1))
        painter.restore()

    def _paint_key(self, painter: QPainter, rect: QRectF, model, s: float) -> None:
        mode = model.mode
        radius = rect.height() / 2.0
        tint = _tok.AMBER if mode == "armed" else (255, 255, 255)
        # Pure glass: almost no tint, strong bulge and edge refraction, a little dispersion.
        alpha = {"idle": 0.07, "busy": 0.08, "armed": 0.16, "dim": 0.05}.get(mode, 0.07)
        extra = {}
        if _glass is not None:
            try:
                extra["strength"] = _glass.default_bezel(int(rect.width()), int(rect.height())) * 1.0
            except Exception:
                extra = {}
        if not self._draw_lens(painter, rect, radius, tint=tint, tint_alpha=alpha, saturation=1.22,
                               bulge=0.15, spec_gain=1.3, dispersion=0.12, **extra):
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.fillPath(path, QColor(255, 255, 255, 50 if mode == "dim" else 90))
            painter.setPen(QPen(QColor(255, 255, 255, 170), 1.0))
            painter.drawPath(path)

        k = 0.5 if mode == "dim" else 1.0
        fade = self._content_alpha()
        # Engraved key outline: no fill, so the glass shows through it.
        cap_h = rect.height() * 0.54
        cap_font = _font(9 * s, QFont.Weight.DemiBold, spacing=0.6)
        painter.setFont(cap_font)
        cap_w = max(cap_h * 1.25, painter.fontMetrics().horizontalAdvance(model.key) + 24.0 * s)
        cap = QRectF(rect.left() + radius * 0.6, rect.center().y() - cap_h / 2.0, cap_w, cap_h)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._ink(int(95 * k)), 1.3))
        painter.drawRoundedRect(cap, 10.0 * s, 10.0 * s)
        self._draw_text(painter, cap, model.key, cap_font, self._ink(int(225 * k), 1))

        pad_r = radius * 0.62 + (26.0 * s if mode == "busy" else 0.0)
        tx = cap.right() + 14.0 * s
        tw = max(10.0, rect.right() - pad_r - tx)
        self._draw_text(painter, QRectF(tx, rect.top() + rect.height() * 0.15, tw, rect.height() * 0.38), model.label,
                        _font(11.5 * s, QFont.Weight.DemiBold), self._ink(int(240 * k * fade)),
                        Qt.AlignmentFlag.AlignLeft)
        self._draw_text(painter, QRectF(tx, rect.top() + rect.height() * 0.52, tw, rect.height() * 0.30), model.sub,
                        _font(9 * s, QFont.Weight.Normal), self._ink(int(175 * k * fade), 1),
                        Qt.AlignmentFlag.AlignLeft)

        if mode == "busy":
            d = 18.0 * s
            arc = QRectF(rect.right() - radius * 0.62 - d, rect.center().y() - d / 2.0, d, d)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._accent(70), 2.4 * s))
            painter.drawEllipse(arc)
            pen = QPen(self._accent(230), 2.4 * s)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(arc, int(-((self._phase * 1080.0) % 360.0) * 16), 100 * 16)
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1.5, radius + 1.5)
            sweep_x = rect.left() + rect.width() * ((self._phase * 1.8) % 1.0)
            sweep = QLinearGradient(sweep_x - 55.0, rect.top(), sweep_x + 55.0, rect.top())
            sweep.setColorAt(0.0, self._accent(0))
            sweep.setColorAt(0.5, self._accent(190))
            sweep.setColorAt(1.0, QColor(100, 210, 255, 0))
            painter.setPen(QPen(sweep, 2.0))
            painter.drawPath(path)
        elif mode == "armed":
            ring = 0.5 + 0.5 * math.sin(self._phase * math.tau * 3.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(*_tok.AMBER, int(110 + 120 * ring)), 2.2))
            painter.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0), radius + 1.0, radius + 1.0)

        flashed = self._key_flash.get(model.key)
        if flashed is not None:
            elapsed = time.monotonic() - flashed
            if elapsed < 0.22:
                path = QPainterPath()
                path.addRoundedRect(rect, radius, radius)
                painter.fillPath(path, QColor(255, 255, 255, int(150 * (1.0 - elapsed / 0.22))))

    def _paint_corner_status(self, painter: QPainter) -> None:
        lay = self._layout()
        s = lay.scale
        rect = QRectF(*lay.status)
        painter.save()
        if not self._draw_lens(painter, rect, rect.height() / 2.0, tint_alpha=0.52, spec_gain=0.9, bulge=0.04):
            path = QPainterPath()
            path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
            painter.fillPath(path, QColor(255, 255, 255, 120))
            painter.setPen(QPen(QColor(255, 255, 255, 170), 1.0))
            painter.drawPath(path)
        label = self._status_label()
        font = _font(8.5 * s, QFont.Weight.DemiBold, spacing=1.1)
        painter.setFont(font)
        text_w = painter.fontMetrics().horizontalAdvance(label)
        dot_d = 7.0 * s
        gap = 10.0 * s
        left = rect.left() + (rect.width() - (dot_d + gap + text_w)) / 2.0
        pulse = 0.5 + 0.5 * math.sin(self._phase * math.tau * 2.0)
        halo = QRadialGradient(QPointF(left + dot_d / 2.0, rect.center().y()), dot_d * 1.9)
        halo.setColorAt(0.0, self._accent(int(90 + 70 * pulse)))
        halo.setColorAt(1.0, self._accent(0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(left + dot_d / 2.0, rect.center().y()), dot_d * 1.9, dot_d * 1.9)
        painter.setBrush(self._accent(235))
        painter.drawEllipse(QRectF(left, rect.center().y() - dot_d / 2.0, dot_d, dot_d))
        self._draw_text(painter, QRectF(left + dot_d + gap, rect.top(), text_w + 4.0, rect.height()), label, font,
                        self._ink(215, 1), Qt.AlignmentFlag.AlignLeft)
        painter.restore()

    def _paint_brand_signature(self, painter: QPainter) -> None:
        lay = self._layout()
        rect = QRectF(*lay.brand)
        painter.save()
        if not self._draw_lens(painter, rect, rect.height() / 2.0, tint_alpha=0.30, spec_gain=0.9, bulge=0.04):
            path = QPainterPath()
            path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
            painter.fillPath(path, QColor(255, 255, 255, 100))
            painter.setPen(QPen(QColor(255, 255, 255, 160), 1.0))
            painter.drawPath(path)
        self._draw_text(painter, rect.adjusted(14.0 * lay.scale, 0, -14.0 * lay.scale, 0), "Secured by MajestyGuard",
                        _font(8.5 * lay.scale, QFont.Weight.Medium, spacing=0.5), self._ink(190, 1))
        painter.restore()

    def _status_label(self) -> str:
        return _layout.status_label(self._state.name)


    def _request_verification(self, source: str) -> None:
        self._mark_key("SPACE")
        if self._state.name not in {"locked_passive", "soft_locked", "social_lock", "camera_fault"}:
            return
        if self._on_verify_requested is not None:
            self._on_verify_requested(source)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if False:
            self._request_verification("overlay_click")
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key.Key_Tab:
            self._on_tab_pressed()
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self._tab_gate.reset()
            self._request_verification("overlay_key")
            event.accept()
            return
        event.accept()

    def focusNextPrevChild(self, next: bool) -> bool:
        return False

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        event.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._allow_close:
            _uninstall_hooks()
            _set_taskbar_visible(True)
            event.accept()
        else:
            event.ignore()
            self.showFullScreen()
            self._force_topmost()
            self.raise_()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if self.isVisible() and event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.showFullScreen()
            self._force_topmost()
            self.raise_()
            self.activateWindow()
        super().changeEvent(event)

    def event(self, event) -> bool:  # type: ignore[override]
        if self.isVisible() and event.type() == QEvent.Type.WindowDeactivate:
            self._force_topmost()
            self.raise_()
            self.activateWindow()
            return True
        return super().event(event)
