"""Painted building blocks for the Control Center (no business logic).

Everything is drawn with QPainter so the window needs no image assets and stays
crisp at any display scale. Colours come from the shared design tokens, so the
Control Center, the lock overlay and the island read as one product.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QAbstractButton, QWidget

try:  # package layout in the repo / bundle, flat layout when run from ui/
    from ui import tokens as tok
except ImportError:  # pragma: no cover
    import tokens as tok

INK = QColor(*tok.INK)
INK_SOFT = QColor(*tok.INK_SOFT)
INK_FAINT = QColor(*tok.INK_FAINT)
BLUE = QColor(*tok.BLUE)
GREEN = QColor(*tok.GREEN)
AMBER = QColor(*tok.AMBER)
RED = QColor(*tok.RED)
CYAN = QColor(*tok.CYAN)

TONE_COLOUR = {"ok": GREEN, "info": BLUE, "warn": AMBER, "bad": RED, "idle": QColor(134, 144, 163)}


def with_alpha(colour: QColor, alpha: int) -> QColor:
    out = QColor(colour)
    out.setAlpha(max(0, min(255, int(alpha))))
    return out


class AuroraBackground(QWidget):
    """Same luminous colour field as the lock overlay, rendered once per size."""

    _ORBS = (
        (0.06, 0.08, 0.60, (112, 168, 255, 170)),
        (0.94, 0.06, 0.52, (186, 138, 255, 150)),
        (0.84, 0.96, 0.64, (255, 126, 178, 140)),
        (0.04, 0.94, 0.48, (104, 214, 232, 130)),
        (0.50, 0.50, 0.40, (255, 206, 150, 70)),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cache = QPixmap()

    def _render(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        pix = QPixmap(w, h)
        painter = QPainter(pix)
        base = QLinearGradient(0.0, 0.0, 0.0, float(h))
        base.setColorAt(0.0, QColor(248, 249, 255))
        base.setColorAt(1.0, QColor(222, 229, 247))
        painter.fillRect(0, 0, w, h, base)
        for cx, cy, radius, (r, g, b, a) in self._ORBS:
            glow = QRadialGradient(QPointF(cx * w, cy * h), radius * max(w, h))
            glow.setColorAt(0.0, QColor(r, g, b, a))
            glow.setColorAt(1.0, QColor(r, g, b, 0))
            painter.fillRect(0, 0, w, h, glow)
        painter.end()
        self._cache = pix

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._cache.size() != self.size():
            self._render()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        painter.end()


class GlassPanel(QWidget):
    """A frosted card: translucent white body, bright top rim, soft shadow line."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 22.0, strength: float = 1.0) -> None:
        super().__init__(parent)
        self._radius = radius
        self._strength = strength
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        body = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        s = self._strength
        body.setColorAt(0.0, QColor(255, 255, 255, int(165 * s)))
        body.setColorAt(1.0, QColor(246, 249, 255, int(120 * s)))
        painter.fillPath(path, body)
        rim = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        rim.setColorAt(0.0, QColor(255, 255, 255, 235))
        rim.setColorAt(0.55, QColor(255, 255, 255, 120))
        rim.setColorAt(1.0, QColor(120, 132, 160, 76))
        painter.setPen(QPen(rim, 1.0))
        painter.drawPath(path)
        painter.end()


class StatusOrb(QWidget):
    """Breathing status badge: a coloured core ringed by dots."""

    def __init__(self, size: int = 56, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(134, 144, 163)
        self._animated = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_state_visual(self, color_hex: str, *, animated: bool) -> None:
        self._color = QColor(color_hex)
        self._animated = animated
        if animated and not self._timer.isActive():
            self._timer.start()
        elif not animated and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.05) % math.tau
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        u = self.width() / 46.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        pulse = 0.5 + 0.5 * math.sin(self._phase) if self._animated else 0.3
        aura = QRadialGradient(QPointF(cx, cy), 22.0 * u)
        aura.setColorAt(0.0, with_alpha(self._color, 35 + 45 * pulse))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(QPointF(cx, cy), 22.0 * u, 22.0 * u)
        painter.setBrush(self._color)
        r = (5.0 + 0.9 * pulse) * u
        painter.drawEllipse(QPointF(cx, cy), r, r)
        dot = QColor(42, 52, 70)
        for i in range(8):
            ang = i / 8.0 * math.tau + (self._phase * 0.6 if self._animated else 0.0)
            rad = (13.5 + (1.2 * math.sin(self._phase + i) if self._animated else 0.0)) * u
            dot.setAlpha(170 if i % 2 == 0 else 105)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad), 1.7 * u, 1.7 * u)
        painter.end()


def draw_icon(painter: QPainter, kind: str, rect: QRectF, colour: QColor) -> None:
    """Small line icons for the navigation rail, all on a 24x24 grid."""
    painter.save()
    s = rect.width() / 24.0
    painter.translate(rect.topLeft())
    painter.scale(s, s)
    pen = QPen(colour, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "overview":
        for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
            painter.drawRoundedRect(QRectF(x, y, 7, 7), 2.2, 2.2)
    elif kind == "protection":
        path = QPainterPath(QPointF(12, 2.8))
        path.cubicTo(16.5, 5.2, 19.2, 5.6, 20.2, 5.8)
        path.lineTo(20.2, 12.2)
        path.cubicTo(20.2, 16.8, 16.6, 19.9, 12, 21.4)
        path.cubicTo(7.4, 19.9, 3.8, 16.8, 3.8, 12.2)
        path.lineTo(3.8, 5.8)
        path.cubicTo(4.8, 5.6, 7.5, 5.2, 12, 2.8)
        painter.drawPath(path)
        painter.drawPolyline([QPointF(8.4, 12.2), QPointF(11, 14.8), QPointF(15.8, 9.4)])
    elif kind == "camera":
        painter.drawRoundedRect(QRectF(3, 6.5, 18, 12.5), 3.4, 3.4)
        painter.drawEllipse(QPointF(12, 12.7), 3.6, 3.6)
        painter.drawLine(QPointF(8, 6.5), QPointF(9.4, 4.2))
        painter.drawLine(QPointF(9.4, 4.2), QPointF(14.6, 4.2))
        painter.drawLine(QPointF(14.6, 4.2), QPointF(16, 6.5))
    elif kind == "activity":
        painter.drawPolyline([QPointF(2.8, 12.4), QPointF(7, 12.4), QPointF(9.6, 5.6), QPointF(14, 19),
                              QPointF(16.6, 12.4), QPointF(21.2, 12.4)])
    elif kind == "enrollment":
        painter.drawEllipse(QPointF(12, 12), 8.6, 8.6)
        painter.drawEllipse(QPointF(12, 10.2), 2.7, 2.7)
        arc = QPainterPath(QPointF(6.6, 18.2))
        arc.cubicTo(8, 14.8, 16, 14.8, 17.4, 18.2)
        painter.drawPath(arc)
    elif kind == "about":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.drawLine(QPointF(12, 11), QPointF(12, 16.4))
        painter.setPen(QPen(colour, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPoint(QPointF(12, 7.6))
    painter.restore()


class NavButton(QAbstractButton):
    """Sidebar tab: icon + label, a glass pill when selected, keyboard focusable."""

    def __init__(self, kind: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoExclusive(True)
        self._badge = ""
        self._badge_tone = "warn"

    def set_badge(self, text: str, tone: str = "warn") -> None:
        if (text, tone) != (self._badge, self._badge_tone):
            self._badge, self._badge_tone = text, tone
            self.update()

    def badge(self) -> str:
        return self._badge

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(200, 44)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        selected = self.isChecked()
        hover = self.underMouse() and self.isEnabled()
        if selected or hover or self.hasFocus():
            path = QPainterPath()
            path.addRoundedRect(rect, 14, 14)
            if selected:
                fill = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
                fill.setColorAt(0.0, QColor(255, 255, 255, 215))
                fill.setColorAt(1.0, QColor(255, 255, 255, 150))
                painter.fillPath(path, fill)
                painter.setPen(QPen(QColor(255, 255, 255, 235), 1.0))
                painter.drawPath(path)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(BLUE)
                painter.drawRoundedRect(QRectF(rect.left() + 3, rect.center().y() - 9, 3.2, 18), 1.6, 1.6)
            else:
                painter.fillPath(path, QColor(255, 255, 255, 90 if hover else 0))
                if self.hasFocus():
                    painter.setPen(QPen(with_alpha(BLUE, 150), 1.4))
                    painter.drawPath(path)
        colour = BLUE if selected else INK_SOFT
        draw_icon(painter, self._kind, QRectF(rect.left() + 16, rect.center().y() - 10, 20, 20), colour)
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(INK if selected else INK_SOFT)
        painter.drawText(QRectF(rect.left() + 48, rect.top(), rect.width() - 80, rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())
        if self._badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(TONE_COLOUR.get(self._badge_tone, AMBER))
            painter.drawEllipse(QPointF(rect.right() - 16, rect.center().y()), 4.5, 4.5)
        painter.end()


class Toggle(QAbstractButton):
    """iOS-style switch. A real checkable button, so keyboard and screen readers work."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.toggled.connect(lambda _c: self._timer.start())

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    position = pyqtProperty(float, _get_pos, _set_pos)

    def setChecked(self, checked: bool) -> None:  # type: ignore[override]
        super().setChecked(checked)
        self._pos = 1.0 if checked else 0.0   # no animation for programmatic sync
        self._timer.stop()
        self.update()

    def _step(self) -> None:
        target = 1.0 if self.isChecked() else 0.0
        self._pos += (target - self._pos) * 0.35
        if abs(target - self._pos) < 0.02:
            self._pos = target
            self._timer.stop()
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        t = self._pos
        off, on = QColor(150, 160, 182, 120), BLUE
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t), int(off.alpha() + (on.alpha() - off.alpha()) * t),
        )
        if not self.isEnabled():
            track.setAlpha(int(track.alpha() * 0.45))
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        d = rect.height() - 6
        x = rect.left() + 3 + (rect.width() - d - 6) * t
        painter.setPen(QPen(QColor(120, 132, 160, 70), 1.0))
        painter.setBrush(QColor(255, 255, 255, 255 if self.isEnabled() else 170))
        painter.drawEllipse(QRectF(x, rect.top() + 3, d, d))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(with_alpha(BLUE, 170), 1.6))
            painter.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), rect.height() / 2 + 1.5, rect.height() / 2 + 1.5)
        painter.end()


class Dot(QWidget):
    """Tiny coloured status dot used in lists and chips."""

    def __init__(self, tone: str = "info", size: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size + 4, size + 4)
        self._tone = tone

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = TONE_COLOUR.get(self._tone, BLUE)
        c = QPointF(self.width() / 2, self.height() / 2)
        halo = QRadialGradient(c, self.width() / 2)
        halo.setColorAt(0.0, with_alpha(colour, 90))
        halo.setColorAt(1.0, with_alpha(colour, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(c, self.width() / 2, self.width() / 2)
        painter.setBrush(colour)
        painter.drawEllipse(c, 3.6, 3.6)
        painter.end()


def make_tray_icon(accent_hex: str) -> QIcon:
    """Crisp 64x64 vector shield/orb icon for the notification area."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    painter.fillPath(bg, QColor("#0B0F17"))
    painter.setPen(QPen(QColor(accent_hex), 2.5))
    painter.drawPath(bg)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(accent_hex))
    painter.drawEllipse(QPointF(32, 32), 7.5, 7.5)
    ring = QColor("#F2F4F8")
    ring.setAlpha(210)
    painter.setBrush(ring)
    for idx in range(6):
        angle = idx * 1.04719755
        r = 15.5 * (1.0 if idx % 2 == 0 else 0.92)
        painter.drawEllipse(QPointF(32 + r * math.cos(angle), 32 + r * math.sin(angle)), 2.4, 2.4)
    painter.end()
    return QIcon(pixmap)
