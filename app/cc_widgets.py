"""Painted building blocks for the Control Center (no business logic).

Solid, theme-driven surfaces and gradients (no translucency tricks). Everything
reads the active theme from ``app.theme`` at paint time, so a theme switch is just
a repaint, and everything is drawn with QPainter so no image assets are needed and
it stays crisp at any display scale.
"""
from __future__ import annotations

import math
import re

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
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
from PyQt6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from app import theme as _theme

_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)")


def qcolor(value: str, alpha: float | None = None) -> QColor:
    """'#RRGGBB' or 'rgba(r,g,b,a)' -> QColor (optionally with a 0..1 alpha override)."""
    m = _RGBA.fullmatch(value.strip())
    colour = QColor(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(float(m.group(4)) * 255)) if m else QColor(value)
    if alpha is not None:
        colour.setAlphaF(max(0.0, min(1.0, alpha)))
    return colour


def with_alpha(colour: QColor, alpha: int) -> QColor:
    out = QColor(colour)
    out.setAlpha(max(0, min(255, int(alpha))))
    return out


def tone_colour(tone: str) -> QColor:
    t = _theme.active()
    return qcolor({"ok": t.ok, "info": t.info, "warn": t.warn, "bad": t.bad}.get(tone, t.text_faint))


def accent_gradient(x0: float, y0: float, x1: float, y1: float, alpha: float = 1.0) -> QLinearGradient:
    t = _theme.active()
    grad = QLinearGradient(x0, y0, x1, y1)
    grad.setColorAt(0.0, qcolor(t.accent_a, alpha))
    grad.setColorAt(1.0, qcolor(t.accent_b, alpha))
    return grad


class Backdrop(QWidget):
    """Window backdrop: a quiet vertical gradient with two large accent glows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cache = QPixmap()
        self._key: tuple = ()

    def invalidate(self) -> None:
        self._key = ()
        self.update()

    def _render(self) -> None:
        t = _theme.active()
        w, h = max(1, self.width()), max(1, self.height())
        pix = QPixmap(w, h)
        painter = QPainter(pix)
        base = QLinearGradient(0.0, 0.0, 0.0, float(h))
        base.setColorAt(0.0, qcolor(t.bg_top))
        base.setColorAt(1.0, qcolor(t.bg_bottom))
        painter.fillRect(0, 0, w, h, base)
        strength = 0.30 if t.dark else 0.22
        for (cx, cy, rad, col) in ((0.98, 0.0, 0.62, t.glow_a), (0.0, 1.0, 0.55, t.glow_b)):
            glow = QRadialGradient(QPointF(cx * w, cy * h), rad * max(w, h))
            glow.setColorAt(0.0, qcolor(col, strength))
            glow.setColorAt(1.0, qcolor(col, 0.0))
            painter.fillRect(0, 0, w, h, glow)
        painter.end()
        self._cache = pix
        self._key = (t.key, w, h)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._key != (_theme.active().key, self.width(), self.height()):
            self._render()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        painter.end()


class Surface(QWidget):
    """A solid card: theme surface, hairline border, faint top sheen."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 20.0, raised: bool = False) -> None:
        super().__init__(parent)
        self._radius = radius
        self._raised = raised

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        t = _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, qcolor(t.surface_hi if self._raised else t.surface))
        sheen = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + rect.height() * 0.5)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 14 if t.dark else 70))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, sheen)
        painter.setPen(QPen(qcolor(t.border), 1.0))
        painter.drawPath(path)
        painter.end()


class GradientCard(QWidget):
    """The hero: a vivid diagonal gradient with soft decorative rings."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 26.0) -> None:
        super().__init__(parent)
        self._radius = radius
        self._a = QColor("#4F7CFF")
        self._b = QColor("#9B5CFF")

    def set_colors(self, a_hex: str, b_hex: str) -> None:
        self._a, self._b = QColor(a_hex), QColor(b_hex)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, self._a)
        grad.setColorAt(1.0, self._b)
        painter.fillPath(path, grad)
        painter.setClipPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i, (rx, ry, r) in enumerate(((0.93, -0.10, 0.62), (1.02, 0.78, 0.44), (0.70, 1.20, 0.55))):
            painter.setPen(QPen(QColor(255, 255, 255, 30 - i * 6), 1.4))
            painter.drawEllipse(QPointF(rect.width() * rx, rect.height() * ry), rect.height() * r, rect.height() * r)
        shine = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        shine.setColorAt(0.0, QColor(255, 255, 255, 40))
        shine.setColorAt(0.5, QColor(255, 255, 255, 0))
        shine.setColorAt(1.0, QColor(0, 0, 0, 28))
        painter.fillPath(path, shine)
        painter.end()


class StatusRing(QWidget):
    """Hero status badge: ring track, an orbiting arc while active, shield in the middle."""

    def __init__(self, size: int = 68, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._animated = False
        self._phase = 0.0
        self._ink = QColor("#FFFFFF")
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_state_visual(self, color_hex: str, *, animated: bool) -> None:  # color kept for API parity
        self._animated = animated
        if animated and not self._timer.isActive():
            self._timer.start()
        elif not animated and self._timer.isActive():
            self._timer.stop()
        self.update()

    def set_ink(self, colour: QColor) -> None:
        self._ink = colour
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.035) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(with_alpha(self._ink, 70), 4.0))
        painter.drawEllipse(rect)
        if self._animated:
            pen = QPen(self._ink, 4.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(-self._phase * 360 * 16), 110 * 16)
        icon = QRectF(rect.center().x() - 13, rect.center().y() - 13, 26, 26)
        draw_icon(painter, "protection", icon, self._ink)
        painter.end()


class LogoMark(QWidget):
    def __init__(self, size: int = 42, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        t = _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, rect.width() * 0.3, rect.width() * 0.3)
        painter.fillPath(path, accent_gradient(rect.left(), rect.top(), rect.right(), rect.bottom()))
        pad = rect.width() * 0.24
        draw_icon(painter, "protection", rect.adjusted(pad, pad, -pad, -pad), qcolor(t.on_accent))
        painter.end()


class HeroBadge(QWidget):
    """White line icon in a translucent disc, for use on a gradient hero card."""

    def __init__(self, kind: str, size: int = 60, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._kind = kind

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 46))
        painter.drawEllipse(rect)
        pad = rect.width() * 0.27
        draw_icon(painter, self._kind, rect.adjusted(pad, pad, -pad, -pad), QColor("#FFFFFF"))
        painter.end()


class IconChip(QWidget):
    """Small rounded square holding a line icon, tinted with the accent gradient."""

    def __init__(self, kind: str, size: int = 38, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._kind = kind

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        t = _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.fillPath(path, accent_gradient(rect.left(), rect.top(), rect.right(), rect.bottom(), 0.20))
        pad = rect.width() * 0.26
        draw_icon(painter, self._kind, rect.adjusted(pad, pad, -pad, -pad), qcolor(t.accent_a))
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
    elif kind == "appearance":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.setPen(QPen(colour, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x, y in ((8, 9.2), (12, 7), (16, 9.6), (15.4, 15)):
            painter.drawPoint(QPointF(x, y))
    elif kind == "clock":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.drawPolyline([QPointF(12, 6.8), QPointF(12, 12), QPointF(15.8, 14.2)])
    elif kind == "unlock":
        painter.drawRoundedRect(QRectF(4.5, 10.5, 15, 10), 3, 3)
        arc = QPainterPath(QPointF(8, 10.5))
        arc.lineTo(8, 7.4)
        arc.cubicTo(8, 2.4, 16, 2.4, 16.6, 6.4)
        painter.drawPath(arc)
    elif kind == "about":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.drawLine(QPointF(12, 11), QPointF(12, 16.4))
        painter.setPen(QPen(colour, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPoint(QPointF(12, 7.6))
    painter.restore()


class NavButton(QAbstractButton):
    """Sidebar tab: icon + label; the selected one is a solid accent-gradient pill."""

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
        self._kbd_focus = False

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        self._kbd_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def set_badge(self, text: str, tone: str = "warn") -> None:
        if (text, tone) != (self._badge, self._badge_tone):
            self._badge, self._badge_tone = text, tone
            self.update()

    def badge(self) -> str:
        return self._badge

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(200, 44)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        t = _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 2, -1, -2)
        selected = self.isChecked()
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        if selected:
            painter.fillPath(path, accent_gradient(rect.left(), rect.top(), rect.right(), rect.bottom()))
            ink, label = qcolor(t.on_accent), qcolor(t.on_accent)
        else:
            if self.underMouse() and self.isEnabled():
                painter.fillPath(path, qcolor(t.surface_hi))
            if self.hasFocus() and self._kbd_focus:
                painter.setPen(QPen(qcolor(t.accent_a), 1.6))
                painter.drawPath(path)
            ink, label = qcolor(t.text_soft), qcolor(t.text)
        draw_icon(painter, self._kind, QRectF(rect.left() + 15, rect.center().y() - 10, 20, 20), ink)
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Bold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(label)
        painter.drawText(QRectF(rect.left() + 47, rect.top(), rect.width() - 78, rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())
        if self._badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tone_colour(self._badge_tone))
            painter.drawEllipse(QPointF(rect.right() - 15, rect.center().y()), 4.5, 4.5)
        painter.end()


class Toggle(QAbstractButton):
    """Switch with an accent-gradient track. A real checkable button: keyboard + a11y work."""

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
        t = _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = rect.height() / 2
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, qcolor(t.surface_hi))
        painter.setOpacity(self._pos if self.isEnabled() else self._pos * 0.4)
        painter.fillPath(path, accent_gradient(rect.left(), rect.top(), rect.right(), rect.top()))
        painter.setOpacity(1.0 if self.isEnabled() else 0.5)
        painter.setPen(QPen(qcolor(t.border), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        d = rect.height() - 7
        x = rect.left() + 3.5 + (rect.width() - d - 7) * self._pos
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(x, rect.top() + 3.5, d, d))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor(t.accent_a), 1.8))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)
        painter.end()


class Dot(QWidget):
    """Coloured status dot with a soft halo, for lists and banners."""

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
        colour = tone_colour(self._tone)
        c = QPointF(self.width() / 2, self.height() / 2)
        halo = QRadialGradient(c, self.width() / 2)
        halo.setColorAt(0.0, with_alpha(colour, 100))
        halo.setColorAt(1.0, with_alpha(colour, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(c, self.width() / 2, self.width() / 2)
        painter.setBrush(colour)
        painter.drawEllipse(c, self.width() / 2 - 3.4, self.width() / 2 - 3.4)
        painter.end()


class ThemeSwatch(QAbstractButton):
    """A live miniature of a theme. Click to apply; the selected one gets an accent ring."""

    def __init__(self, theme: "_theme.Theme", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(138, 150)
        self.setFixedHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{theme.label} theme")

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        mine, now = self.theme, _theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        outer = QPainterPath()
        outer.addRoundedRect(rect, 18, 18)
        painter.fillPath(outer, qcolor(now.surface))
        # the miniature window
        mini = QRectF(rect.left() + 10, rect.top() + 10, rect.width() - 20, 82)
        mp = QPainterPath()
        mp.addRoundedRect(mini, 10, 10)
        bg = QLinearGradient(mini.topLeft(), mini.bottomLeft())
        bg.setColorAt(0.0, qcolor(mine.bg_top))
        bg.setColorAt(1.0, qcolor(mine.bg_bottom))
        painter.fillPath(mp, bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(mine.surface))
        painter.drawRoundedRect(QRectF(mini.left() + 6, mini.top() + 6, 26, mini.height() - 12), 6, 6)
        hero = QRectF(mini.left() + 38, mini.top() + 6, mini.width() - 44, 34)
        hp = QPainterPath()
        hp.addRoundedRect(hero, 7, 7)
        g = QLinearGradient(hero.topLeft(), hero.bottomRight())
        g.setColorAt(0.0, qcolor(mine.accent_a))
        g.setColorAt(1.0, qcolor(mine.accent_b))
        painter.fillPath(hp, g)
        for i in range(2):
            tile = QRectF(mini.left() + 38 + i * ((mini.width() - 44) / 2 + 2), mini.top() + 46,
                          (mini.width() - 44) / 2 - 2, 28)
            painter.setBrush(qcolor(mine.surface))
            painter.drawRoundedRect(tile, 6, 6)
        painter.setBrush(qcolor(mine.text_soft))
        painter.drawRoundedRect(QRectF(mini.left() + 46, mini.top() + 50, 34, 4), 2, 2)
        painter.setBrush(qcolor(mine.text))
        painter.drawRoundedRect(QRectF(mini.left() + 46, mini.top() + 60, 22, 6), 3, 3)
        # caption
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(qcolor(now.text))
        painter.drawText(QRectF(rect.left() + 14, mini.bottom() + 6, rect.width() - 28, 22),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.theme.label)
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(qcolor(now.text_soft))
        painter.drawText(QRectF(rect.left() + 14, mini.bottom() + 26, rect.width() - 28, 16),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.theme.tagline)
        selected = self.isChecked()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(qcolor(now.accent_a) if selected else qcolor(now.border), 2.4 if selected else 1.0))
        painter.drawPath(outer)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent_gradient(rect.right() - 34, rect.top() + 8, rect.right() - 10, rect.top() + 32))
            painter.drawEllipse(QRectF(rect.right() - 34, rect.top() + 14, 22, 22))
            painter.setPen(QPen(qcolor(now.on_accent), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPolyline([QPointF(rect.right() - 28, rect.top() + 25), QPointF(rect.right() - 24, rect.top() + 29),
                                  QPointF(rect.right() - 18, rect.top() + 21)])
        elif self.hasFocus():
            painter.setPen(QPen(qcolor(now.accent_a), 1.6))
            painter.drawPath(outer)
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
