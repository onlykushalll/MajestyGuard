"""Hands-free enrollment window: one camera circle inside a coverage ring.

The ring has a faint arc for every head direction. An arc fills (blue -> green)
as real, distinct views in that direction are collected; a small dot glides to
the direction the user should turn toward next. There is no capture button.
The window is a thin view: all decisions come from ``Feedback`` objects made
by ``enrollment_coverage.CoverageCollector``.

Thread model: the camera worker emits ``preview_signal`` / ``feedback_signal`` /
``result_signal`` from its own thread; Qt queues them onto the GUI thread.
"""
from __future__ import annotations

import math
import os
import time
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QPushButton, QStackedWidget, QVBoxLayout, QLabel, QWidget

try:
    from ui.brand import BrandTrace, brand_icon
except ModuleNotFoundError:  # Direct source launch through the ui directory.
    from brand import BrandTrace, brand_icon

try:
    from fonts import ui_font as _ui_font
except Exception:  # pragma: no cover
    _ui_font = None

try:
    from enrollment_coverage import SECTOR_ANGLE
except Exception:  # pragma: no cover
    SECTOR_ANGLE = {}

CUE_TEXT = {
    "center": "Look straight at the camera",
    "left": "Slowly turn your head to the left",
    "right": "Slowly turn your head to the right",
    "up": "Lift your chin a little",
    "down": "Lower your chin a little",
    "up_left": "Look up and a little to the left",
    "up_right": "Look up and a little to the right",
    "down_left": "Look down and a little to the left",
    "down_right": "Look down and a little to the right",
    "tilt_left": "Tilt your head gently to the left",
    "tilt_right": "Tilt your head gently to the right",
    "closer": "Move a little closer",
    "back": "Lean back a little",
    "": "Keep moving slowly",
}

REASON_TEXT = {
    "no_face": "Move into the camera frame",
    "multiple_faces": "Only one person can enroll at a time",
    "quality": "Use even light and hold still",
    "detection": "Face the camera",
    "liveness": "Keep your face uncovered and facing the screen",
    "move_closer": "Move a little closer",
    "move_back": "Move back a little",
    "center": "Center your face in the circle",
    "identity_mismatch": "Please keep to one person",
    "hold_still": "Hold still for a moment",
    "identity_unstable": "Hold still, starting again",
    "no_embedding": "Hold still while the camera focuses",
}

_BLUE = QColor(127, 178, 255)
_GREEN = QColor(95, 211, 154)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def _font(size: float, weight: int = 400):
    if _ui_font is not None:
        return _ui_font(size, QFont.Weight(weight))

    f = QFont("Segoe UI", int(size))
    f.setWeight(QFont.Weight(weight))
    return f


def reduced_motion() -> bool:
    return any(os.environ.get(key) == "1" for key in ("MG_UI_REDUCE_MOTION", "MG_REDUCED_MOTION"))


class CoverageRing(QWidget):
    """Camera circle + coverage arcs + guidance dot."""

    SPAN = 34.0  # degrees per arc

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self._preview: Optional[QImage] = None
        self._fill: dict = {}
        self._progress = 0.0
        self._calibration = 0.0
        self._state = "calibrating"
        self._target: Optional[str] = None
        self._dot_angle: Optional[float] = None
        self._dot_goal: Optional[float] = None
        self._flash = 0.0
        self._pulse = 0.0
        self._last_tick = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ---- state in -----------------------------------------------------------

    def set_preview(self, image: Optional[QImage]) -> None:
        self._preview = image
        self.update()

    def set_feedback(self, fb) -> None:
        self._state = fb.state
        self._fill = dict(getattr(fb, "fill", {}) or {})
        self._progress = float(fb.progress)
        self._calibration = float(getattr(fb, "calibration", 0.0))
        target = getattr(fb, "target", None)
        self._target = target
        goal = None
        if target in SECTOR_ANGLE and SECTOR_ANGLE[target][1] != 2:
            goal = SECTOR_ANGLE[target][0]
        self._dot_goal = goal
        if goal is not None and self._dot_angle is None:
            self._dot_angle = goal
        if fb.accepted and not reduced_motion():
            self._flash = 1.0
        self._sync_motion()
        self.update()

    def _sync_motion(self) -> None:
        if reduced_motion() or self._state != "collecting":
            self._dot_angle = self._dot_goal
            self._flash = 0.0
            self._timer.stop()
        elif self.isVisible() and not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_motion()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    # ---- animation ------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        self._pulse = (self._pulse + 2.1 * dt) % math.tau
        self._flash = max(0.0, self._flash - 3.6 * dt)
        if self._dot_goal is not None and self._dot_angle is not None:
            delta = (self._dot_goal - self._dot_angle + 180.0) % 360.0 - 180.0
            self._dot_angle += delta * -math.expm1(-9.4 * dt)
        self.update()

    # ---- paint ----------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 10.0
        cam_r = r * 0.68

        self._paint_camera(p, cx, cy, cam_r)
        self._paint_center_ring(p, cx, cy, cam_r)
        self._paint_arcs(p, cx, cy, r)
        self._paint_progress(p, cx, cy, r)
        self._paint_dot(p, cx, cy, r)
        p.end()

    def _paint_camera(self, p: QPainter, cx: float, cy: float, cam_r: float) -> None:
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), cam_r, cam_r)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(cx - cam_r, cy - cam_r, cam_r * 2, cam_r * 2), QColor(16, 20, 28))
        img = self._preview
        if img is not None and not img.isNull():
            scale = max(cam_r * 2 / img.width(), cam_r * 2 / img.height())
            dw, dh = img.width() * scale, img.height() * scale
            p.drawImage(QRectF(cx - dw / 2, cy - dh / 2, dw, dh), img)
        p.restore()

    def _paint_center_ring(self, p: QPainter, cx: float, cy: float, cam_r: float) -> None:
        frac = float(self._fill.get("center", 0.0))
        color = _mix(_BLUE, _GREEN, frac)
        color.setAlphaF(0.55 + 0.45 * frac)
        pen = QPen(color, 3.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        extra = self._flash * 4.0
        p.drawEllipse(QPointF(cx, cy), cam_r + 3 + extra, cam_r + 3 + extra)
        if self._target == "center" and not reduced_motion() and self._state == "collecting":
            glow = QColor(_BLUE)
            glow.setAlphaF(0.25 + 0.2 * math.sin(self._pulse))
            p.setPen(QPen(glow, 6.0))
            p.drawEllipse(QPointF(cx, cy), cam_r + 10, cam_r + 10)

    def _arc_radius(self, r: float, layer: int) -> float:
        return r * (0.80 if layer == 0 else 0.90)

    def _paint_arcs(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        done = self._state == "complete"
        for bucket, (angle, layer) in SECTOR_ANGLE.items():
            if layer == 2:
                continue
            rad = self._arc_radius(r, layer)
            rect = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            start = angle - self.SPAN / 2.0
            track = QColor(255, 255, 255, 34)
            p.setPen(QPen(track, 5.0 if layer == 0 else 7.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, int(start * 16), int(self.SPAN * 16))
            frac = 1.0 if done else float(self._fill.get(bucket, 0.0))
            if frac > 0.0:
                col = _mix(_BLUE, _GREEN, frac)
                p.setPen(QPen(col, 5.0 if layer == 0 else 7.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(rect, int(start * 16), int(self.SPAN * frac * 16))

    def _paint_progress(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        rad = r * 0.985
        rect = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
        value = self._calibration if self._state == "calibrating" else self._progress
        p.setPen(QPen(QColor(255, 255, 255, 22), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        if value > 0.0:
            col = _mix(QColor(190, 205, 230), _GREEN, value if self._state != "calibrating" else 0.0)
            p.setPen(QPen(col, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-value * 360.0 * 16))

    def _paint_dot(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        if self._state != "collecting" or self._dot_angle is None or self._dot_goal is None:
            return
        layer = SECTOR_ANGLE.get(self._target, (0.0, 1))[1]
        rad = self._arc_radius(r, layer)
        a = math.radians(self._dot_angle)
        x, y = cx + rad * math.cos(a), cy - rad * math.sin(a)
        halo = QColor(255, 255, 255, int(46 + 30 * (0.5 + 0.5 * math.sin(self._pulse))))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(x, y), 13.0, 13.0)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(QPointF(x, y), 5.0, 5.0)


class CoverageEnrollmentWindow(QWidget):
    cancel_requested = pyqtSignal()
    preview_signal = pyqtSignal(object)
    feedback_signal = pyqtSignal(object)
    result_signal = pyqtSignal(bool, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MajestyGuard enrollment")
        self.setWindowIcon(brand_icon())
        local = os.environ.get("LOCALAPPDATA")
        if local:
            preferences = QSettings(os.path.join(local, "MajestyGuard", "appearance.ini"),
                                    QSettings.Format.IniFormat)
            self.setProperty("ccReduceMotion", preferences.value("reduceMotion", False, type=bool))
        self.setMinimumWidth(320)
        self.setStyleSheet("background:#0E1116;")
        self.finished = False
        self.last_feedback = None

        self.ring = CoverageRing(self)
        self._visuals = QStackedWidget(self)
        self._visuals.addWidget(self.ring)
        prep = QWidget(self._visuals)
        prep_box = QVBoxLayout(prep)
        prep_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_mark = BrandTrace(prep, size=150)
        prep_box.addWidget(self._load_mark)
        self._visuals.addWidget(prep)
        self.title = QLabel("Look straight at the camera", self)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setFont(_font(17, 500))
        self.title.setStyleSheet("color:#F3F6FB;background:transparent;")
        self.hint = QLabel("", self)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setFont(_font(11, 400))
        self.hint.setStyleSheet("color:#9AA6B8;background:transparent;")
        self.percent = QLabel("0%", self)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.percent.setFont(_font(11, 500))
        self.percent.setStyleSheet("color:#7F8CA0;background:transparent;")
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setFont(_font(11, 500))
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setStyleSheet(
            "QPushButton{color:#C8D1E0;background:rgba(255,255,255,0.07);border:1px solid rgba(255,255,255,0.14);"
            "border-radius:16px;padding:8px 26px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.12);}"
            "QPushButton:pressed{background:rgba(255,255,255,0.18);}"
        )
        self.cancel_button.clicked.connect(self._on_cancel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(self._visuals, 1)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addWidget(self.percent)
        lay.addSpacing(6)
        lay.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.resize(min(520, area.width() - 32), min(680, area.height() - 56))

        self.preview_signal.connect(self.set_preview)
        self.feedback_signal.connect(self.apply_feedback)
        self.result_signal.connect(self.show_result)

    # ---- slots ----------------------------------------------------------------

    def set_loading(self, loading: bool) -> None:
        self._load_mark.set_busy(loading)
        self._visuals.setCurrentIndex(int(loading))
        if loading:
            self.percent.clear()

    def set_preview(self, image) -> None:
        if not self.finished:
            if self._visuals.currentIndex() != 0:
                self.set_loading(False)
            self.ring.set_preview(image)

    def apply_feedback(self, fb) -> None:
        if self.finished:
            return
        if self._visuals.currentIndex() != 0:
            self.set_loading(False)
        self.last_feedback = fb
        self.ring.set_feedback(fb)
        self.percent.setText(f"{int(fb.progress * 100)}%" if fb.state != "calibrating" else "")
        if fb.state == "calibrating":
            self.title.setText("Look straight at the camera")
            self.hint.setText(REASON_TEXT.get(fb.reason, "Hold still for a moment"))
        elif fb.state == "collecting":
            self.title.setText(CUE_TEXT.get(fb.cue, CUE_TEXT[""]))
            self.hint.setText("" if fb.accepted else REASON_TEXT.get(fb.reason, ""))
        elif fb.state == "complete":
            self.title.setText("Checking your scan")
            self.hint.setText("")

    def show_result(self, ok: bool, message: str) -> None:
        self.finished = True
        self.set_loading(False)
        self.ring.set_preview(None)
        self.title.setText("Face enrolled" if ok else "Enrollment not saved")
        self.hint.setText(message)
        self.percent.setText("100%" if ok else "")
        self.cancel_button.setText("Close")
        self.ring.set_feedback(_ResultFeedback(ok))

    def _on_cancel(self) -> None:
        if self.finished:
            self.close()
            return
        self.cancel_requested.emit()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self.finished:
            self.cancel_requested.emit()
        super().closeEvent(event)


class _ResultFeedback:
    def __init__(self, ok: bool) -> None:
        self.state = "complete" if ok else "cancelled"
        self.fill = {}
        self.progress = 1.0 if ok else 0.0
        self.calibration = 1.0
        self.target = None
        self.accepted = False
