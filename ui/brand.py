"""Local MajestyGuard monogram, icon sizes and native loading motion."""
from __future__ import annotations

import math
import os

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget


def _strokes():
    # Keep the coordinates identical to assets/majestyguard.svg.
    first = QPainterPath(QPointF(40, 162))
    first.lineTo(40, 38)
    first.lineTo(82, 88)
    first.cubicTo(100, 26, 154, 18, 156, 84)
    second = QPainterPath(QPointF(82, 108))
    second.lineTo(82, 126)
    second.cubicTo(82, 150, 97, 164, 119, 164)
    second.cubicTo(141, 164, 156, 150, 156, 126)
    second.lineTo(156, 114)
    second.lineTo(120, 114)
    return first, second


_STROKES = _strokes()
_TRACES = tuple(tuple(path.pointAtPercent(path.percentAtLength(path.length() * i / 64))
                      for i in range(65)) for path in _STROKES)


def _pen(color, width):
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def paint_mark(painter, rect, color="#EDF2F4", phase=None):
    size = min(rect.width(), rect.height())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x()-size/2, rect.center().y()-size/2)
    painter.scale(size/200, size/200)
    painter.translate(2, -1)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(color, 14))
    for path in _STROKES:
        painter.drawPath(path)
    if phase is not None:
        for offset, trace, tint in ((0, _TRACES[0], "#77EFD9"), (.27, _TRACES[1], "#95BAFF")):
            head = ((phase + offset) % 1.0) * 1.32 - .04
            for i, point in enumerate(trace[1:], 1):
                age = head - i / 64
                if 0 <= age < .23:
                    color = QColor(tint)
                    strength = (1-age/.23)
                    color.setAlphaF(strength * .9)
                    painter.setPen(_pen(color, 5 + strength*7))
                    painter.drawLine(trace[i-1], point)
    painter.restore()


def icon_image(size: int, accent="#B9C7CB"):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    edge = max(1., size*.026)
    plate = QRectF(edge, edge, size-2*edge, size-2*edge)
    p.setBrush(QColor("#172027"))
    p.setPen(_pen(accent, edge))
    p.drawRoundedRect(plate, size*.23, size*.23)
    paint_mark(p, QRectF(-size*.05, -size*.05, size*1.10, size*1.10))
    p.end()
    return image


def brand_icon(accent="#B9C7CB"):
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(icon_image(size, accent)))
    return icon


class BrandTrace(QWidget):
    """A small loading indicator. No animation while hidden or idle."""

    def __init__(self, parent=None, *, size=32, ink="#DCE6EC"):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._ink = ink
        self._busy = False
        self._once = False
        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self, interval=32)
        self._timer.timeout.connect(self._tick)

    def set_busy(self, busy):
        self._once = False
        self._busy = bool(busy)
        self._sync()

    def play_once(self):
        self._once = True
        self._busy = True
        self._elapsed.start()
        self._sync()

    def _tick(self):
        if self._once and self._elapsed.elapsed() >= 1480:
            self.set_busy(False)
        else:
            self.update()

    def _sync(self):
        if (self._busy and self.isVisible() and os.environ.get("MG_UI_REDUCE_MOTION") != "1"
                and not self.window().property("ccReduceMotion")):
            if not self._timer.isActive():
                self._elapsed.start()
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event):
        self._timer.stop()
        if self._once:
            self._once = self._busy = False
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        phase = None
        rect = QRectF(self.rect())
        if self._timer.isActive():
            phase = (self._elapsed.elapsed() % 1480) / 1480
            if self.width() > 60:
                ring = rect.adjusted(6, 6, -6, -6)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(_pen("#394B57", 1))
                p.drawEllipse(ring)
                p.setPen(_pen("#77EFD9", 1.5))
                p.drawArc(ring, round(-phase*5760), 54*16)
                inset = 10 + 2*math.cos(phase*math.tau)
                rect.adjust(inset, inset, -inset, -inset)
        paint_mark(p, rect, self._ink, phase)
