"""Painted building blocks for the Control Center (no business logic).
Everything is drawn with QPainter so the window needs no image assets and stays
crisp at any display scale. Colours come from the shared design tokens, so the
Control Center, the lock overlay and the island read as one product.
"""
from __future__ import annotations

import math
import time
from collections import deque

from app.cc_theme import reduced_motion, theme_for
from ui.brand import brand_icon as make_tray_icon

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, pyqtProperty
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
from PyQt6.QtWidgets import QAbstractButton, QPushButton, QSizePolicy, QWidget

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


class WorkspaceBackground(QWidget):
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme_for(self).bg))


class MemoryTrace(QWidget):
    """Time-based, bounded trace. Unknown or unsampled time is a gap, not zero."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self.setAccessibleName("Protection worker memory over the last minute")
        self.setToolTip("Last minute while Diagnostics is open. Gaps mean no measurement.")
        self._points = deque(maxlen=64)

    def observe(self, value, now=None):
        now = time.monotonic() if now is None else now
        while self._points and now - self._points[0][0] > 60:
            self._points.popleft()
        if self._points and now - self._points[-1][0] < 1:
            self._points.pop()
        self._points.append((now, value))
        self.setAccessibleDescription("Reading unavailable" if value is None else f"{value / 1048576:.1f} MiB")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme = theme_for(self)
        rect = QRectF(self.rect()).adjusted(3, 6, -3, -6)
        p.setPen(QPen(QColor(theme.line), 1, Qt.PenStyle.DotLine))
        for fraction in (0, .5, 1):
            y = rect.top() + rect.height() * fraction
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        if not self._points:
            return
        latest = self._points[-1][0]
        peak = max([1048576] + [value for _, value in self._points if value is not None])
        p.setPen(QPen(QColor(theme.accent), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        last = None
        for stamp, value in self._points:
            if value is None:
                last = None
                continue
            point = QPointF(rect.right() - (latest - stamp) / 60 * rect.width(),
                            rect.bottom() - value / peak * rect.height())
            if last and stamp - last[0] <= 5:
                p.drawLine(last[1], point)
            else:
                p.drawPoint(point)
            last = (stamp, point)


class Surface(QWidget):
    """Flat instrument surface. Only the status band carries a coloured edge."""

    def __init__(self, parent=None, *, radius=8.0):
        super().__init__(parent)
        self._radius = radius

    def paintEvent(self, _event) -> None:
        p = theme_for(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        if self.objectName() == "sidebar":
            painter.fillRect(self.rect(), QColor(p.rail))
            painter.setPen(QColor(p.line))
            painter.drawLine(rect.topRight(), rect.bottomRight())
            return
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, QColor(p.panel))
        band = self.objectName() == "statusBand"
        if band:
            ink = QColor(self.window().property("statusInk") or p.accent)
            wash = QLinearGradient(rect.topRight(), rect.topLeft())
            wash.setColorAt(0, with_alpha(ink, 44))
            wash.setColorAt(.8, with_alpha(ink, 0))
            painter.fillPath(path, wash)
            # A cutaway of the aperture, confined to the unused upper-right margin.
            # These are lens contours, not a graph of recognition confidence.
            if rect.width() >= 570:
                painter.save()
                painter.setClipPath(path)
                painter.setClipRect(QRectF(rect.right()-138, 0, 138, 98), Qt.ClipOperation.IntersectClip)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                origin = rect.topRight() + QPointF(36, -32)
                for step in range(12):
                    r = 42 + 8 * step
                    painter.setPen(QPen(with_alpha(QColor(p.muted), 58 - 4 * step), 1))
                    painter.drawEllipse(origin, r, r)
                painter.restore()
        painter.setPen(QPen(QColor(p.line), 1))
        painter.drawPath(path)
        if band:
            painter.setClipPath(path)
            painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()), ink)


class StatusOrb(QWidget):
    """A sensor dial; only engine startup needs a continuously moving marker."""

    def __init__(self, size: int = 56, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(134, 144, 163)
        self._animated = False
        self._symbol = "stopped"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_state_visual(self, color_hex: str, *, animated: bool, symbol: str = "stopped") -> None:
        if (self._symbol, self._color, self._animated) == (symbol, QColor(color_hex), animated):
            return
        self._symbol = symbol
        self._color = QColor(color_hex)
        self._animated = animated
        self._sync_timer()
        self.update()

    def _sync_timer(self):
        animate = self._animated and self.isVisible() and not reduced_motion(self)
        if animate and not self._timer.isActive():
            self._timer.start()
        elif not animate:
            self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

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
        painter.translate(cx, cy)
        painter.scale(u, u)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        dot = QColor(theme_for(self).muted)
        for i in range(44):
            angle = math.tau * i / 44
            major = i % 11 == 0
            painter.setPen(QPen(with_alpha(dot, 190 if major else 80), .85))
            vx, vy = math.sin(angle), -math.cos(angle)
            painter.drawLine(QPointF(vx * (17.6 if major else 19), vy * (17.6 if major else 19)),
                             QPointF(vx * 20.8, vy * 20.8))
        # Six iris blades, drawn around a clear centre for the actual state symbol.
        # Only startup turns the iris; a running guard is not necessarily using the camera.
        painter.save()
        if self._symbol == "starting":
            painter.rotate(math.degrees(self._phase))
        blade = QPainterPath(QPointF(-1, -16.5))
        blade.cubicTo(4.5, -17.1, 9.9, -14.3, 13.7, -9.0)
        blade.lineTo(5.0, -7.2)
        blade.cubicTo(2.4, -10.5, .4, -13.5, -1, -16.5)
        blade.closeSubpath()
        for _ in range(6):
            sheen = QLinearGradient(-1, -17, 10, -7)
            sheen.setColorAt(0, with_alpha(self._color, 160))
            sheen.setColorAt(1, with_alpha(self._color, 40))
            painter.fillPath(blade, sheen)
            painter.rotate(60)
        painter.restore()
        pen = QPen(QColor(theme_for(self).text), 1.7, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if self._symbol == "running":
            painter.drawPolyline([QPointF(-5, 0), QPointF(-1.5, 3.5), QPointF(5.5, -4)])
        elif self._symbol == "paused":
            for x in (-2.5, 2.5):
                painter.drawLine(QPointF(x, -4), QPointF(x, 4))
        elif self._symbol == "degraded":
            painter.drawLine(QPointF(0, -5), QPointF(0, 1))
            painter.drawPoint(QPointF(0, 4.5))
        elif self._symbol == "starting":
            painter.drawArc(QRectF(-5, -5, 10, 10), int(-self._phase * 180 / math.pi * 16), 225 * 16)
        elif self._symbol == "enroll":
            painter.drawLine(QPointF(0, -4.5), QPointF(0, 4.5))
            painter.drawLine(QPointF(-4.5, 0), QPointF(4.5, 0))
        else:
            painter.drawLine(QPointF(-4.5, 0), QPointF(4.5, 0))
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
    elif kind == "diagnostics":
        painter.drawRoundedRect(QRectF(3, 4, 18, 13), 2, 2)
        painter.drawLine(QPointF(9, 21), QPointF(15, 21))
        painter.drawLine(QPointF(12, 17), QPointF(12, 21))
        for x, height in ((8, 3), (12, 6), (16, 4)):
            painter.drawLine(QPointF(x, 13), QPointF(x, 13 - height))
    elif kind == "enrollment":
        painter.drawEllipse(QPointF(12, 12), 8.6, 8.6)
        painter.drawEllipse(QPointF(12, 10.2), 2.7, 2.7)
        arc = QPainterPath(QPointF(6.6, 18.2))
        arc.cubicTo(8, 14.8, 16, 14.8, 17.4, 18.2)
        painter.drawPath(arc)
    elif kind == "appearance":
        painter.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        painter.setBrush(colour)
        painter.drawPie(QRectF(3.5, 3.5, 17, 17), 90 * 16, 180 * 16)
    elif kind == "about":
        painter.drawEllipse(QPointF(12, 12), 9, 9)
        painter.drawLine(QPointF(12, 11), QPointF(12, 16.4))
        painter.setPen(QPen(colour, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPoint(QPointF(12, 7.6))
    painter.restore()


class TransportButton(QPushButton):
    """Use two quadrilaterals for both shapes: a play triangle unfolds into pause bars."""

    def __init__(self, parent):
        super().__init__(parent)
        self._phase = 0.
        self._target = None
        self._tween = QPropertyAnimation(self, b"transportPhase", self)
        self._tween.setDuration(185)
        self._tween.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setIconSize(QSize(18, 18))

    def _draw(self, phase):
        self._phase = phase
        pix = QPixmap(self.iconSize() * self.devicePixelRatioF())
        pix.setDevicePixelRatio(self.devicePixelRatioF())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#122838" if self.objectName() == "plateAction" else theme_for(self).ink))
        p.setPen(Qt.PenStyle.NoPen)
        for start, end in (
            (((3,2),(9,5.5),(9,12.5),(3,16)), ((3,2),(7,2),(7,16),(3,16))),
            (((9,5.5),(15,9),(15,9),(9,12.5)), ((11,2),(15,2),(15,16),(11,16))),
        ):
            path = QPainterPath()
            for i, ((x,y),(xx,yy)) in enumerate(zip(start, end)):
                point = QPointF(x+(xx-x)*phase, y+(yy-y)*phase)
                (path.moveTo if i == 0 else path.lineTo)(point)
            path.closeSubpath()
            p.drawPath(path)
        p.end()
        self.setIcon(QIcon(pix))

    transportPhase = pyqtProperty(float, lambda self: self._phase, _draw)

    def setText(self, label):
        if label == self.text():
            return
        super().setText(label)
        old = self._target
        self._target = {"Pause protection": 1., "Resume protection": 0., "Start protection": 0.,
                        "Restart protection": 0.}.get(label)
        self._tween.stop()
        if self._target is None:
            self.setIcon(QIcon())
        elif old is not None and self._phase != self._target and self.isVisible() and not reduced_motion(self):
            self._tween.setStartValue(self._phase)
            self._tween.setEndValue(self._target)
            self._tween.start()
        else:
            self._draw(self._target)

    def showEvent(self, event):
        super().showEvent(event)
        if self._target is not None:
            self._draw(self._target)

    def hideEvent(self, event):
        self._tween.stop()
        super().hideEvent(event)


class TabStrip(QWidget):
    """One moving selection underline; the page beneath it never needs compositing."""

    def __init__(self, parent):
        super().__init__(parent)
        self._target = None
        self._line = QWidget(self)
        self._line.setObjectName("tabUnderline")
        self._line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._motion = QPropertyAnimation(self._line, b"geometry", self)
        self._motion.setDuration(205)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)

    def select(self, button, animate=True):
        old = self._target
        self._target = button
        rect = button.geometry().adjusted(10, button.height()-3, -10, 0)
        self._line.raise_()
        self._motion.stop()
        if old is not None and old is not button and animate and self.isVisible() and not reduced_motion(self):
            self._motion.setStartValue(self._line.geometry())
            self._motion.setEndValue(rect)
            self._motion.start()
        else:
            self._line.setGeometry(rect)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._target:
            self.layout().activate()
            self.select(self._target, False)


class NavButton(QAbstractButton):
    """A labelled top tab. At compact widths only the optional icon is removed."""

    def __init__(self, kind: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoExclusive(True)
        self._badge = ""
        self._badge_tone = "warn"
        self.compact = False

    def set_badge(self, text: str, tone: str = "warn") -> None:
        if (text, tone) != (self._badge, self._badge_tone):
            self._badge, self._badge_tone = text, tone
            self.setAccessibleDescription("Needs attention" if text else "")
            self.update()

    def badge(self) -> str:
        return self._badge

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(100, 44)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        selected = self.isChecked()
        hover = self.underMouse() and self.isEnabled()
        palette = theme_for(self)
        painter.setBrush(QColor(palette.raised) if hover else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(palette.accent), 1) if self.hasFocus() else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        colour = QColor(palette.accent if selected else palette.muted)
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(palette.text if selected else palette.muted))
        width = painter.fontMetrics().horizontalAdvance(self.text())
        with_icon = not self.compact and width + 38 <= rect.width()
        start = rect.center().x() - (width + (23 if with_icon else 0)) / 2
        if with_icon:
            draw_icon(painter, self._kind, QRectF(start, rect.center().y()-8, 16, 16), colour)
            start += 23
        painter.drawText(QRectF(start, rect.top(), width+2, rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())
        if self._badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(TONE_COLOUR.get(self._badge_tone, AMBER))
            point = rect.topRight() + QPointF(-5, 7)
            painter.drawEllipse(point, 3.5, 3.5)
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
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(145)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._move_thumb)

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    position = pyqtProperty(float, _get_pos, _set_pos)

    def setChecked(self, checked: bool) -> None:  # type: ignore[override]
        if self.isChecked() == checked:
            return  # A status poll must not cut short an in-flight user gesture.
        super().setChecked(checked)
        self._settle()

    def _settle(self):
        self._animation.stop()
        self.position = float(self.isChecked())

    def _move_thumb(self, checked):
        self._animation.stop()
        if not self.isVisible() or reduced_motion(self):
            self._settle()
            return
        self._animation.setStartValue(self.position)
        self._animation.setEndValue(float(checked))
        self._animation.start()

    def hideEvent(self, event):
        self._settle()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        t = self._pos
        palette = theme_for(self)
        off, on = QColor(palette.line), QColor(palette.accent)
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
        off_thumb, on_thumb = QColor(palette.text), QColor(palette.ink)
        painter.setBrush(QColor.fromRgbF(*(a + (b-a)*t for a, b in zip(off_thumb.getRgbF()[:3], on_thumb.getRgbF()[:3]))))
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


class EventMarker(QWidget):
    """One segment of the activity timeline. The node aligns with the row title."""

    def __init__(self, tone, *, first, last, parent):
        super().__init__(parent)
        self._tone, self._first, self._last = tone, first, last
        self.setFixedWidth(18)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_segment(self, tone, first, last):
        if (self._tone, self._first, self._last) != (tone, first, last):
            self._tone, self._first, self._last = tone, first, last
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y = self.width() / 2, 18.
        p.setPen(QPen(QColor(theme_for(self).line), 1))
        if not self._first:
            p.drawLine(QPointF(x, 0), QPointF(x, y-5))
        if not self._last:
            p.drawLine(QPointF(x, y+5), QPointF(x, self.height()))
        colour = TONE_COLOUR.get(self._tone, BLUE)
        p.setPen(QPen(colour, 1.5))
        p.setBrush(colour if self._first else QColor(theme_for(self).bg))
        p.drawEllipse(QPointF(x, y), 3.1, 3.1)
