"""Vector camera shutter for the desk and tray. No camera access or polling."""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QImageReader, QLinearGradient, QMovie, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget

from app.cc_theme import reduced_motion

_REEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "protection-reel.webp"
_INTRO_PATH = _REEL_PATH.with_name("protection-intro.webp")
_REEL_SPEED = {"starting": 100, "running": 75}


def _polar(r, angle):
    return QPointF(r * math.cos(angle), r * math.sin(angle))


class Aperture(QWidget):
    """Report camera state separately from decorative protection motion."""

    intro_finished = pyqtSignal()

    def __init__(self, parent, *, miniature=False):
        super().__init__(parent)
        self._opening = 0.0
        self._reading = None
        self._mini = miniature
        self._mode = None
        self._movie = None
        self._intro = False
        self._intro_last_frame = -1
        self._handoff = QTimer(self, singleShot=True, interval=0)
        self._handoff.timeout.connect(self._finish_intro)
        self._closing = False
        self._last_image = None
        self._still = QImage()
        self._art_opacity = 0.0
        self._fade = QPropertyAnimation(self, b"artOpacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        if miniature:
            self.setFixedSize(76, 76)
        else:
            self.setMinimumHeight(155)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Camera shutter diagram")
        self._travel = QPropertyAnimation(self, b"opening", self)
        self._travel.setDuration(420)
        self._travel.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _set_opening(self, amount):
        self._opening = amount
        self.update()

    opening = pyqtProperty(float, lambda self: self._opening, _set_opening)

    def _set_art_opacity(self, amount):
        self._art_opacity = amount
        self.update()

    artOpacity = pyqtProperty(float, lambda self: self._art_opacity, _set_art_opacity)

    def set_protection_art(self, mode, *, skip_intro=False):
        if self._mini or (self._mode == mode and not (skip_intro and self._intro)):
            return
        if skip_intro or mode is None:
            self._intro = False
            self._handoff.stop()
        elif mode == "starting":
            self._intro = self.isVisible() and not self.window().isMinimized() and not reduced_motion(self)
        self._closing = mode is None and self._art_opacity > 0
        if self._closing and self._movie is not None:
            self._last_image = self._movie.currentPixmap()
        self._mode = mode
        self._sync_art()

    def _frame_changed(self, frame):
        self.update()
        if self._intro and frame == self._intro_last_frame:
            self._handoff.start()

    def _finish_intro(self):
        if self._intro:
            self._intro = False
            self._sync_art()
            self.intro_finished.emit()

    def _sync_art(self):
        speed = _REEL_SPEED.get(self._mode)
        visible = self.isVisible() and not self.window().isMinimized()
        if speed and visible and self._movie is None:
            self._movie = QMovie(self)
            self._movie.frameChanged.connect(self._frame_changed)
            self._still = QImageReader(str(_REEL_PATH)).read()
        use_art = bool(speed and not self._still.isNull())
        motion = visible and not reduced_motion(self)
        if not motion:
            self._intro = False
        if self._movie is not None:
            if use_art:
                path = (_INTRO_PATH if self._intro else _REEL_PATH).as_posix()
                if self._movie.fileName() != path:
                    self._movie.stop()
                    self._movie.setFileName(path)
                    if self._intro and not self._movie.isValid():
                        self._intro = False
                        self._movie.setFileName(str(_REEL_PATH))
                    self._intro_last_frame = self._movie.frameCount() - 1 if self._intro else -1
            if use_art and motion:
                self._movie.setSpeed(100 if self._intro else speed)
                if self._movie.state() == QMovie.MovieState.NotRunning:
                    self._movie.start()
            elif self._movie.state() != QMovie.MovieState.NotRunning:
                self._movie.stop()
        target = float(use_art)
        if self._fade.state() == QPropertyAnimation.State.Running and motion and self._fade.endValue() == target:
            return
        self._fade.stop()
        if motion and self._art_opacity != target:
            self._fade.setStartValue(self._art_opacity)
            self._fade.setDuration(380 if self._closing else 180)
            self._fade.setEndValue(target)
            self._fade.start()
        else:
            self.artOpacity = target

    def sync_motion_preference(self):
        if reduced_motion(self):
            self._settle()
        if not self._mini:
            self._sync_art()

    def set_reading(self, reading):
        if self._reading == reading:
            return
        self._reading = reading
        self.setAccessibleDescription({"open": "Camera in use", "parked": "Camera parked",
                                      "fault": "Camera needs attention"}.get(reading, "No live camera reading"))
        self._settle(self.isVisible() and not reduced_motion(self))
        self.update()

    def _settle(self, animate=False):
        target = 1.0 if self._reading == "open" else 0.0
        self._travel.stop()
        if animate and self._opening != target:
            self._travel.setStartValue(self._opening)
            self._travel.setEndValue(target)
            self._travel.start()
        else:
            self.opening = target

    def hideEvent(self, event):
        self._settle()
        self._intro = self._closing = False
        self._handoff.stop()
        if self._movie is not None:
            self._movie.stop()
        self._fade.stop()
        self.artOpacity = float(self._mode in _REEL_SPEED and not self._still.isNull())
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._mini:
            self._sync_art()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width()/2, self.height()/2)
        size = min(self.width(), self.height(), 276)
        scale = size / 220
        if self._closing and self._art_opacity:
            p.save()
            p.scale(scale, scale)
            self._paint_shutter(p)
            p.restore()
        if self._art_opacity:
            is_still = reduced_motion(self) or self._movie is None
            pixmap = None if is_still else self._movie.currentPixmap()
            p.save()
            if self._closing:
                if self._last_image is not None:
                    pixmap = self._last_image
                # A rotating seven-sided opening reveals the stationary shutter underneath.
                k = self._art_opacity
                path = QPainterPath()
                path.addPolygon(QPolygonF([_polar(size * .79 * k, i * math.tau / 7 + (1-k)*.58)
                                           for i in range(7)]))
                path.closeSubpath()
                p.setClipPath(path)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.setOpacity(1 if self._closing else self._art_opacity)
            area = QRectF(-size/2, -size/2, size, size)
            if pixmap is None or pixmap.isNull():
                p.drawImage(area, self._still)
            else:
                p.drawPixmap(area, pixmap, QRectF(pixmap.rect()))
            p.restore()
        p.scale(scale, scale)
        if self._art_opacity < 1 and not (self._closing and self._art_opacity):
            p.setOpacity(1 - self._art_opacity)
            self._paint_shutter(p)
            p.setOpacity(1.0)
        if not self._mini:
            p.setPen(QPen(QColor("#69757F"), 1))
            for x in (-1, 1):
                for y in (-1, 1):
                    p.drawLine(QPointF(x*100, y*89), QPointF(x*100, y*98))
                    p.drawLine(QPointF(x*100, y*98), QPointF(x*91, y*98))

    def _paint_shutter(self, p):
        tint = QColor({"open": "#82F2CB", "fault": "#FFB775", "parked": "#ADC9ED"}.get(self._reading, "#9EA5AD"))
        # A lathed housing and seven offset blades. The hole is physically drawn
        # between their tips; no permanent sweep or animation timer is needed.
        rim = QLinearGradient(-85, -85, 72, 96)
        for pos, color in ((0, "#9DAAB6"), (.18, "#354049"), (.48, "#11181E"),
                           (.77, "#515F6B"), (1, "#1C262C")):
            rim.setColorAt(pos, QColor(color))
        p.setPen(QPen(QColor("#090E11"), 2))
        p.setBrush(rim)
        p.drawEllipse(QPointF(), 92, 92)
        p.setBrush(QColor("#171E25"))
        p.setPen(QPen(QColor("#63707B"), .8))
        p.drawEllipse(QPointF(), 85, 85)
        for i in range(84):
            a = math.tau * i / 84
            major = not i % 7
            p.setPen(QPen(QColor("#AAB8C3" if major else "#4C5965"), 1.3 if major else .85))
            p.drawLine(_polar(76 if major else 79, a), _polar(82, a))
        glass = QRadialGradient(QPointF(-9, -12), 63)
        glass.setColorAt(0, QColor("#3E746F"))
        glass.setColorAt(.28, QColor("#132C35"))
        glass.setColorAt(1, QColor("#080E13"))
        p.setBrush(glass)
        p.setPen(QPen(QColor("#0A1015"), 3))
        p.drawEllipse(QPointF(), 72, 72)
        hole = 4.5 + 32*self._opening
        for i in range(7):
            a = math.tau*i/7 - 1.6
            step = math.tau/7
            twist = .91 - .38*self._opening
            polygon = [_polar(71, a + step*j/12) for j in range(13)]
            polygon.extend((_polar(hole, a+step+twist), _polar(hole, a+twist)))
            grad = QLinearGradient(_polar(75, a), _polar(12, a+step))
            shade = int(12*math.cos(a+1))
            grad.setColorAt(0, QColor(87+shade, 100+shade, 111+shade))
            grad.setColorAt(1, QColor(33+shade, 42+shade, 51+shade))
            p.setPen(QPen(QColor("#172129"), .85))
            p.setBrush(grad)
            p.drawPolygon(QPolygonF(polygon))
            p.setPen(QPen(QColor(209, 228, 241, 76), .7))
            p.drawLine(polygon[-1], polygon[0])
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(tint, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(-97, -97, 194, 194), 58*16, 40*16)
