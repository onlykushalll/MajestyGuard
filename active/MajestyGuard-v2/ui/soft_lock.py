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

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRect, QRectF, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import (
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
)
from PyQt6.QtWidgets import QApplication, QWidget

from states import IslandState, get_state


_LOCK_NAMES = {"locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock"}

WH_KEYBOARD_LL = 13
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4  # Left Alt
VK_RMENU = 0xA5  # Right Alt
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_F4 = 0x73
VK_D = 0x44
VK_N = 0x4E
VK_A = 0x41


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_hook_id = None
_callback_ref = None  # prevent GC of the callback
_hook_thread = None
_hook_thread_stop = False
_overlay_locked = False


def _is_key_down(vk: int) -> bool:
    """Check if a key is currently held down."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _keyboard_ll_callback(nCode, wParam, lParam):
    """Low-level keyboard hook callback. Returns 1 to block, calls next hook to allow."""
    global _overlay_locked
    if nCode >= 0 and _overlay_locked and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode

        # Block Ctrl+Shift+Esc (Task Manager)
        if vk == VK_ESCAPE and _is_key_down(VK_LCONTROL) and _is_key_down(VK_LSHIFT):
            return 1
        if vk == VK_ESCAPE and _is_key_down(VK_RCONTROL) and _is_key_down(VK_RSHIFT):
            return 1

        # Block Alt+Tab (App Switcher)
        if vk == VK_TAB and (_is_key_down(VK_LMENU) or _is_key_down(VK_RMENU)):
            return 1

        # Block Alt+F4
        if vk == VK_F4 and (_is_key_down(VK_LMENU) or _is_key_down(VK_RMENU)):
            return 1

        # Block Win+key combos (Tab=TaskView, D=Desktop, N=Notifications, A=ActionCenter)
        if _is_key_down(VK_LWIN) or _is_key_down(VK_RWIN):
            if vk in (VK_TAB, VK_D, VK_N, VK_A):
                return 1

        # Block lone Win key press (Start Menu)
        if vk in (VK_LWIN, VK_RWIN):
            return 1

    return ctypes.windll.user32.CallNextHookEx(_hook_id, nCode, wParam, lParam)


def _hook_thread_func():
    """Run the Windows message loop for the keyboard hook in a background thread."""
    global _hook_id, _callback_ref, _hook_thread_stop
    import time as _time
    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p)
    _callback_ref = HOOKPROC(_keyboard_ll_callback)
    _hook_id = ctypes.windll.user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, _callback_ref, None, 0
    )
    if not _hook_id:
        return

    msg = ctypes.wintypes.MSG()
    while not _hook_thread_stop:
        # PeekMessage with PM_REMOVE — non-blocking, keeps hook alive
        if ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1  # PM_REMOVE = 1
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
        else:
            _time.sleep(0.01)  # Yield CPU when no messages


def _install_keyboard_hook() -> None:
    """Install WH_KEYBOARD_LL hook in a background thread."""
    global _overlay_locked, _hook_thread, _hook_thread_stop
    _overlay_locked = True
    if _hook_id is not None:
        return  # Already installed
    _hook_thread_stop = False
    import threading
    _hook_thread = threading.Thread(target=_hook_thread_func, name="mg-kb-hook", daemon=True)
    _hook_thread.start()


def _uninstall_keyboard_hook() -> None:
    """Uninstall the keyboard hook and stop the message loop thread."""
    global _hook_id, _callback_ref, _overlay_locked, _hook_thread_stop, _hook_thread
    _overlay_locked = False
    if _hook_id is not None:
        ctypes.windll.user32.UnhookWindowsHookEx(_hook_id)
        _hook_id = None
        _callback_ref = None
    _hook_thread_stop = True
    _hook_thread = None


def _set_taskbar_visible(visible: bool) -> None:
    """No-op — we don't hide/show the taskbar. Overlay covers it instead."""
    return


atexit.register(lambda: (_uninstall_keyboard_hook(), _set_taskbar_visible(True)))


class SoftLockOverlay(QWidget):
    """Fullscreen, topmost, frameless glass shield for desktop soft-lock."""

    def __init__(self, on_verify_requested=None):
        super().__init__()
        self._state: IslandState = get_state("idle")
        self._on_verify_requested = on_verify_requested
        self._background = QPixmap()
        self._noise = self._build_noise_texture()
        self._phase = 0.0
        self._opacity_value = 0.0
        self._allow_close = False

        self._setup_window()
        self._setup_motion()
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
        self._timer.setInterval(250)
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

    def apply_state(self, state: IslandState) -> None:
        if state.name == self._state.name and self.isVisible():
            self._state = state
            self._force_topmost()
            self.raise_()
            return
        self._state = state
        if state.name in _LOCK_NAMES:
            if not self.isVisible():
                self._opacity_value = 0.0  # Reset opacity before showing to avoid one-frame flash
                self._fit_virtual_screen()
                self._capture_background()
                self.showFullScreen()
                self._force_topmost()
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
                self._timer.start()
                self._animate_opacity(0.0, 1.0)
                _install_keyboard_hook()
                _set_taskbar_visible(False)
            else:
                self._force_topmost()
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.update()
            return

        if state.name not in _LOCK_NAMES:
            _uninstall_keyboard_hook()
            _set_taskbar_visible(True)
            if self.isVisible():
                self.dissolve()

    def _capture_background(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self._background = QPixmap()
            return
        rect = screen.virtualGeometry()
        shot = screen.grabWindow(0)
        if shot.isNull():
            self._background = QPixmap()
            return
        half = shot.scaled(
            max(1, rect.width() // 2),
            max(1, rect.height() // 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        first = half.scaled(
            max(1, rect.width() // 8),
            max(1, rect.height() // 8),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        second = first.scaled(
            max(1, rect.width() // 3),
            max(1, rect.height() // 3),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred = second.scaled(
            rect.width(),
            rect.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Pre-render the glass atmosphere and noise once into a cached QPixmap
        # to avoid extremely heavy linear and radial gradient rasterization at 60fps.
        cache = QPixmap(blurred.size())
        cache.fill(Qt.GlobalColor.transparent)
        painter = QPainter(cache)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Paint blurred background screenshot
        painter.drawPixmap(0, 0, blurred)

        # 2. Paint glass atmosphere
        r = QRect(0, 0, rect.width(), rect.height())
        painter.fillRect(r, QColor(246, 248, 252, 119))

        wash = QLinearGradient(0, 0, 0, r.height())
        wash.setColorAt(0.0, QColor(255, 255, 255, 185))
        wash.setColorAt(0.18, QColor(252, 254, 255, 129))
        wash.setColorAt(0.54, QColor(244, 247, 252, 60))
        wash.setColorAt(0.82, QColor(235, 239, 246, 75))
        wash.setColorAt(1.0, QColor(218, 224, 235, 104))
        painter.fillRect(r, wash)

        # Draw static glass glow overlays
        for x, y, radius, color, alpha in (
            (r.width() * 0.22, r.height() * 0.22, r.width() * 0.44, QColor(255, 255, 255), 66),
            (r.width() * 0.77, r.height() * 0.26, r.width() * 0.34, QColor(205, 235, 255), 38),
            (r.width() * 0.72, r.height() * 0.76, r.width() * 0.38, QColor(255, 226, 238), 31),
            (r.width() * 0.18, r.height() * 0.82, r.width() * 0.30, QColor(222, 233, 255), 25),
        ):
            glow = QRadialGradient(float(x), float(y), float(radius))
            color.setAlpha(alpha)
            glow.setColorAt(0.0, color)
            glow.setColorAt(0.62, QColor(color.red(), color.green(), color.blue(), max(0, alpha // 5)))
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(r, glow)

        sheen = QLinearGradient(0, 0, r.width(), 0)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
        sheen.setColorAt(0.20, QColor(255, 255, 255, 48))
        sheen.setColorAt(0.50, QColor(255, 255, 255, 25))
        sheen.setColorAt(0.80, QColor(255, 255, 255, 44))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(r, sheen)

        band = QLinearGradient(0, r.height() * 0.44, 0, r.height() * 0.60)
        band.setColorAt(0.0, QColor(255, 255, 255, 0))
        band.setColorAt(0.48, QColor(255, 255, 255, 35))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(r, band)

        edge = QLinearGradient(0, 0, 0, r.height())
        edge.setColorAt(0.0, QColor(255, 255, 255, 106))
        edge.setColorAt(0.09, QColor(255, 255, 255, 0))
        edge.setColorAt(0.88, QColor(255, 255, 255, 0))
        edge.setColorAt(1.0, QColor(255, 255, 255, 69))
        painter.fillRect(r, edge)

        painter.fillRect(QRectF(0, 0, r.width(), 1.5), QColor(255, 255, 255, 150))
        painter.fillRect(QRectF(0, 0, 1.5, r.height()), QColor(255, 255, 255, 73))
        painter.fillRect(QRectF(r.width() - 1.5, 0, 1.5, r.height()), QColor(255, 255, 255, 44))
        painter.fillRect(QRectF(0, r.height() - 1.5, r.width(), 1.5), QColor(84, 88, 96, 31))

        shade = QLinearGradient(0, 0, 0, r.height())
        shade.setColorAt(0.0, QColor(0, 0, 0, 0))
        shade.setColorAt(0.72, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(44, 52, 64, 35))
        painter.fillRect(r, shade)

        # 3. Paint noise texture
        if not self._noise.isNull():
            painter.save()
            painter.setOpacity(0.11)
            painter.drawTiledPixmap(r, self._noise)
            painter.restore()

        painter.end()
        self._background = cache

    @staticmethod
    def _build_noise_texture() -> QPixmap:
        image = QImage(192, 192, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        for y in range(image.height()):
            for x in range(image.width()):
                seed = (x * 73856093) ^ (y * 19349663) ^ 0xA7C15
                value = (seed ^ (seed >> 11) ^ (seed >> 23)) & 0xFF
                alpha = 1 + (value % 6)
                shade = 255 if value > 127 else 232
                image.setPixelColor(x, y, QColor(shade, shade, shade, alpha))
        return QPixmap.fromImage(image)

    def dissolve(self) -> None:
        """Fade the entire window out using overlayOpacity instead of windowOpacity (prevents DWM composite thrashing)."""
        self._dissolve_anim.stop()
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
        self._timer.stop()
        self.hide()
        self.setWindowOpacity(1.0)  # reset for next show
        _uninstall_keyboard_hook()
        _set_taskbar_visible(True)

    def _animate_opacity(self, start: float, end: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(start)
        self._fade.setEndValue(end)
        try:
            self._fade.finished.disconnect(self._hide_after_fade)
        except TypeError:
            pass
        self._fade.finished.connect(self._hide_after_fade)
        self._fade.start()

    def _hide_after_fade(self) -> None:
        try:
            self._fade.finished.disconnect(self._hide_after_fade)
        except TypeError:
            pass
        if self._opacity_value <= 0.02:
            self._timer.stop()
            self.hide()
            _uninstall_keyboard_hook()
            _set_taskbar_visible(True)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.008) % 1.0
        # self.update() is omitted to stop the redundant 60fps fullscreen repaint loop
        
        # Hardening: actively close Task Manager if it gets opened
        try:
            user32 = ctypes.windll.user32
            hwnd_taskmgr = user32.FindWindowW("TaskManagerWindow", None)
            if hwnd_taskmgr:
                user32.PostMessageW(hwnd_taskmgr, WM_CLOSE, 0, 0)
        except Exception:
            pass

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

        self._paint_corner_status(painter)
        self._paint_brand_signature(painter)
        painter.end()

    def _paint_blurred_desktop(self, painter: QPainter) -> None:
        if not self._background.isNull():
            painter.drawPixmap(self.rect(), self._background)
        else:
            painter.fillRect(self.rect(), QColor("#E9EDF3"))

    def _paint_glass_atmosphere(self, painter: QPainter) -> None:
        rect = self.rect()
        painter.fillRect(rect, QColor(246, 248, 252, 119))

        wash = QLinearGradient(0, 0, 0, rect.height())
        wash.setColorAt(0.0, QColor(255, 255, 255, 185))
        wash.setColorAt(0.18, QColor(252, 254, 255, 129))
        wash.setColorAt(0.54, QColor(244, 247, 252, 60))
        wash.setColorAt(0.82, QColor(235, 239, 246, 75))
        wash.setColorAt(1.0, QColor(218, 224, 235, 104))
        painter.fillRect(rect, wash)

        pulse = 0.5 + 0.5 * math.sin(self._phase * math.tau)
        for x, y, radius, color, alpha in (
            (rect.width() * 0.22, rect.height() * 0.22, rect.width() * 0.44, QColor(255, 255, 255), 60 + int(12 * pulse)),
            (rect.width() * 0.77, rect.height() * 0.26, rect.width() * 0.34, QColor(205, 235, 255), 38),
            (rect.width() * 0.72, rect.height() * 0.76, rect.width() * 0.38, QColor(255, 226, 238), 31),
            (rect.width() * 0.18, rect.height() * 0.82, rect.width() * 0.30, QColor(222, 233, 255), 25),
        ):
            glow = QRadialGradient(float(x), float(y), float(radius))
            color.setAlpha(alpha)
            glow.setColorAt(0.0, color)
            glow.setColorAt(0.62, QColor(color.red(), color.green(), color.blue(), max(0, alpha // 5)))
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(rect, glow)

        sheen = QLinearGradient(0, 0, rect.width(), 0)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
        sheen.setColorAt(0.20, QColor(255, 255, 255, 48))
        sheen.setColorAt(0.50, QColor(255, 255, 255, 25))
        sheen.setColorAt(0.80, QColor(255, 255, 255, 44))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, sheen)

        band = QLinearGradient(0, rect.height() * 0.44, 0, rect.height() * 0.60)
        band.setColorAt(0.0, QColor(255, 255, 255, 0))
        band.setColorAt(0.48, QColor(255, 255, 255, 35))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, band)

        edge = QLinearGradient(0, 0, 0, rect.height())
        edge.setColorAt(0.0, QColor(255, 255, 255, 106))
        edge.setColorAt(0.09, QColor(255, 255, 255, 0))
        edge.setColorAt(0.88, QColor(255, 255, 255, 0))
        edge.setColorAt(1.0, QColor(255, 255, 255, 69))
        painter.fillRect(rect, edge)

        painter.fillRect(QRectF(0, 0, rect.width(), 1.5), QColor(255, 255, 255, 150))
        painter.fillRect(QRectF(0, 0, 1.5, rect.height()), QColor(255, 255, 255, 73))
        painter.fillRect(QRectF(rect.width() - 1.5, 0, 1.5, rect.height()), QColor(255, 255, 255, 44))
        painter.fillRect(QRectF(0, rect.height() - 1.5, rect.width(), 1.5), QColor(84, 88, 96, 31))

        shade = QLinearGradient(0, 0, 0, rect.height())
        shade.setColorAt(0.0, QColor(0, 0, 0, 0))
        shade.setColorAt(0.72, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(44, 52, 64, 35))
        painter.fillRect(rect, shade)

    def _paint_noise_texture(self, painter: QPainter) -> None:
        if self._noise.isNull():
            return
        painter.save()
        painter.setOpacity(self._opacity_value * 0.11)
        painter.drawTiledPixmap(self.rect(), self._noise)
        painter.restore()

    def _paint_corner_status(self, painter: QPainter) -> None:
        rect = self.rect()
        margin = max(24, min(42, rect.width() // 48))
        pill = QRectF(margin, margin, 176, 34)
        self._paint_corner_pill(painter, pill, self._status_label(), align=Qt.AlignmentFlag.AlignLeft)

    def _paint_brand_signature(self, painter: QPainter) -> None:
        rect = self.rect()
        margin = max(24, min(42, rect.width() // 48))
        pill = QRectF(rect.width() - margin - 218, rect.height() - margin - 34, 218, 34)
        self._paint_corner_pill(
            painter,
            pill,
            "Secured by MajestyGuard",
            align=Qt.AlignmentFlag.AlignHCenter,
            brand=True,
        )

    def _paint_corner_pill(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        *,
        align: Qt.AlignmentFlag,
        brand: bool = False,
    ) -> None:
        if brand:
            text_rect = rect.adjusted(14, 0, -14, 0)
        else:
            path = QPainterPath()
            path.addRoundedRect(rect, 17, 17)
            body = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            body.setColorAt(0.0, QColor(255, 255, 255, 62))
            body.setColorAt(1.0, QColor(238, 241, 247, 42))
            painter.fillPath(path, body)

            border = QColor(172, 182, 194, 80)
            painter.setPen(QPen(border, 1.0))
            painter.drawPath(path)

            dot = QColor(self._state.accent_color)
            dot.setAlpha(128)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(QRectF(rect.left() + 14, rect.center().y() - 3, 6, 6))
            text_rect = rect.adjusted(29, 0, -14, 0)

        color = QColor(82, 86, 94, 118 if brand else 174)
        painter.setPen(color)
        painter.setFont(QFont("Segoe UI Variable Text", 9, QFont.Weight.Medium))
        painter.drawText(
            text_rect,
            align | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

    def _status_label(self) -> str:
        if self._state.name == "verifying_lock":
            return "VERIFYING"
        if self._state.name == "social_lock":
            return "PRIVACY LOCK"
        if self._state.name == "hostile_lock":
            return "SECURITY HOLD"
        return "LOCKED"

    def _request_verification(self, source: str) -> None:
        if self._state.name not in {"locked_passive", "soft_locked", "social_lock"}:
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
        if event.key() == Qt.Key.Key_Space:
            self._request_verification("overlay_key")
        # Consume ALL keys — nothing passes through to desktop while locked
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        event.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._allow_close:
            _uninstall_keyboard_hook()
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
