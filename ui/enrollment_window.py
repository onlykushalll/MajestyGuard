"""Guided, local-only face enrollment window."""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRunnable, QRect, QRectF, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from daemon.enrollment_pose import (
        check_pose_alignment,
        estimate_head_pose_5pt,
        friendly_readiness_message,
        guide_direction_for_angle,
        instruction_for_angle,
        target_pose_for_angle,
    )
except ImportError:
    from enrollment_pose import (
        check_pose_alignment,
        estimate_head_pose_5pt,
        friendly_readiness_message,
        guide_direction_for_angle,
        instruction_for_angle,
        target_pose_for_angle,
    )

try:
    from ui import tokens as _tok
except ImportError:
    import tokens as _tok

# Colours come from the shared design tokens so the studio matches the overlay,
# island and Control Center.
BLUE_HEX = _tok.hex_of(_tok.BLUE)
BLUE_HOVER_HEX = _tok.hex_of(_tok.shade(_tok.BLUE, 0.84))
GREEN_HEX = _tok.hex_of(_tok.GREEN)
GREEN_DEEP_HEX = _tok.hex_of(_tok.shade(_tok.GREEN, 0.82))
GREEN_SOFT_HEX = _tok.hex_of(_tok.shade(_tok.GREEN, 1.18))
AMBER_HEX = _tok.hex_of(_tok.AMBER)
CYAN_HEX = _tok.hex_of(_tok.CYAN)

_AUTO_DWELL_SECONDS = 0.55
_RING_TICKS = 64


class _CaptureSignals(QObject):
    finished = pyqtSignal(object, object, object)


class _CaptureBurstTask(QRunnable):
    def __init__(self, callback: Callable[[str, int], list], angle: str, angle_index: int) -> None:
        super().__init__()
        self._callback = callback
        self._angle = angle
        self._angle_index = angle_index
        self.signals = _CaptureSignals()

    def run(self) -> None:
        try:
            result = self._callback(self._angle, self._angle_index)
            samples = list(result or [])
            status = str(getattr(result, "status", ""))
            self.signals.finished.emit(samples, None, status)
        except Exception as exc:
            self.signals.finished.emit(None, exc, "")


class _LiveAnalysisSignals(QObject):
    finished = pyqtSignal(object, object, object)


class _LiveAnalysisTask(QRunnable):
    def __init__(self, frame: np.ndarray, analyzer: Callable[[np.ndarray], tuple]) -> None:
        super().__init__()
        self._frame = frame
        self._analyzer = analyzer
        self.signals = _LiveAnalysisSignals()

    def run(self) -> None:
        try:
            result = self._analyzer(self._frame)
            self.signals.finished.emit(self._frame, result, None)
        except Exception as exc:
            self.signals.finished.emit(self._frame, None, exc)


def _project_3d_sphere_point(
    lat_rad: float,
    lon_rad: float,
    yaw_rad: float,
    pitch_rad: float,
    roll_rad: float,
) -> tuple[float, float, float]:
    """Rotate a unit-sphere point (lat, lon) by (yaw, pitch, roll) and return (x, y, z)."""
    x0 = math.cos(lat_rad) * math.sin(lon_rad)
    y0 = math.sin(lat_rad)
    z0 = math.cos(lat_rad) * math.cos(lon_rad)

    # Yaw around Y-axis
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    x1 = x0 * cy + z0 * sy
    y1 = y0
    z1 = -x0 * sy + z0 * cy

    # Pitch around X-axis
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    x2 = x1
    y2 = y1 * cp - z1 * sp
    z2 = y1 * sp + z1 * cp

    # Roll around Z-axis
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    x3 = x2 * cr - y2 * sr
    y3 = x2 * sr + y2 * cr
    return x3, y3, z2


class FaceIdRingCanvas(QWidget):
    """Mirrored camera preview with simple alignment and completion guides."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self._frame_image: Optional[QImage] = None
        self._frame_w = 640
        self._frame_h = 480
        self._kps: Optional[np.ndarray] = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._roll_deg = 0.0
        self._observed_yaw = 0.0
        self._observed_pitch = 0.0
        self._observed_roll = 0.0
        self._target_yaw = 0.0
        self._target_pitch = 0.0
        self._ready = False
        self._aligned = False
        self._dwell_progress = 0.0
        self._dwell_started_at: Optional[float] = None
        self._overall_progress = 0.0
        self._capture_flash = 0.0
        self._has_face = False
        self._guide_direction = ""

    def trigger_capture_flash(self) -> None:
        self._capture_flash = 1.0
        self.update()

    def complete_view(self, progress: float) -> None:
        self._overall_progress = float(np.clip(progress, 0.0, 1.0))
        self._ready = False
        self._aligned = False
        self._has_face = False
        self._dwell_progress = 0.0
        self._dwell_started_at = None
        self._capture_flash = 1.0
        self.update()

    def clear_tracking(self) -> None:
        self._ready = False
        self._aligned = False
        self._has_face = False
        self._dwell_progress = 0.0
        self._dwell_started_at = None
        self.update()

    def set_frame_state(
        self,
        bgr_frame: Optional[np.ndarray],
        *,
        yaw: float,
        pitch: float,
        roll_deg: float = 0.0,
        kps: Optional[np.ndarray] = None,
        target_yaw: float,
        target_pitch: float,
        ready: bool,
        aligned: bool,
        dwell_progress: float,
        overall_progress: float,
        guide_direction: str = "",
    ) -> None:
        self._observed_yaw = float(np.clip(yaw, -1.0, 1.0))
        self._observed_pitch = float(np.clip(pitch, -1.0, 1.0))
        self._observed_roll = float(np.clip(roll_deg, -45.0, 45.0))
        self._target_yaw = float(np.clip(target_yaw, -1.0, 1.0))
        self._target_pitch = float(np.clip(target_pitch, -1.0, 1.0))
        self._ready = bool(ready)
        self._aligned = bool(aligned)
        self._dwell_progress = float(np.clip(dwell_progress, 0.0, 1.0))
        self._dwell_started_at = (
            time.monotonic() - self._dwell_progress * _AUTO_DWELL_SECONDS
            if self._dwell_progress > 0.0
            else None
        )
        self._overall_progress = float(np.clip(overall_progress, 0.0, 1.0))
        self._guide_direction = guide_direction
        self._capture_flash = max(0.0, self._capture_flash - 0.08)
        self._has_face = kps is not None

        if kps is not None:
            try:
                arr = np.asarray(kps, dtype=np.float32)
                self._kps = arr.copy() if arr.shape == (5, 2) else None
            except Exception:
                self._kps = None
        else:
            self._kps = None

        if bgr_frame is not None and bgr_frame.size > 0:
            self.set_preview_frame(bgr_frame)
        self.update()

    def set_preview_frame(self, bgr_frame: np.ndarray) -> None:
        if bgr_frame is None or bgr_frame.size == 0:
            return
        self._frame_h, self._frame_w = bgr_frame.shape[:2]
        mirrored = cv2.flip(bgr_frame, 1)
        try:
            rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
            try:
                h, w, ch = rgb.shape
                self._frame_image = QImage(
                    rgb.data, w, h, ch * w, QImage.Format.Format_RGB888
                ).copy()
            finally:
                rgb[:] = 0
        finally:
            mirrored[:] = 0
        self._yaw += (self._observed_yaw - self._yaw) * 0.28
        self._pitch += (self._observed_pitch - self._pitch) * 0.28
        self._roll_deg += (self._observed_roll - self._roll_deg) * 0.28
        self._capture_flash = max(0.0, self._capture_flash - 0.12)
        if self._dwell_started_at is not None:
            self._dwell_progress = min(
                1.0,
                max(0.0, (time.monotonic() - self._dwell_started_at) / _AUTO_DWELL_SECONDS),
            )
        self.update()

    def _paint_legacy(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )

        w = float(self.width())
        h = float(self.height())
        cx = w * 0.5
        cy = h * 0.5
        outer_r = min(w, h) * 0.44
        viewport_r = outer_r - 24.0

        # 1. Ambient radial aura behind the ring
        aura = QRadialGradient(QPointF(cx, cy), outer_r * 1.18)
        if self._capture_flash > 0.05:
            aura.setColorAt(0.0, QColor(52, 199, 89, int(85 * self._capture_flash)))
        elif self._ready and self._aligned:
            aura.setColorAt(0.0, QColor(52, 199, 89, 38))
        else:
            aura.setColorAt(0.0, QColor(100, 210, 255, 24))
        aura.setColorAt(1.0, QColor(8, 10, 15, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(QPointF(cx, cy), outer_r * 1.18, outer_r * 1.18)

        # 2. Circular clipped camera viewport
        clip_path = QPainterPath()
        clip_path.addEllipse(QPointF(cx, cy), viewport_r, viewport_r)
        painter.save()
        painter.setClipPath(clip_path)

        scale = 1.0
        target_rect = QRectF(cx - viewport_r, cy - viewport_r, viewport_r * 2.0, viewport_r * 2.0)
        if self._frame_image is not None and not self._frame_image.isNull():
            img = self._frame_image
            iw, ih = float(img.width()), float(img.height())
            scale = max((viewport_r * 2.0) / max(1.0, iw), (viewport_r * 2.0) / max(1.0, ih))
            dw, dh = iw * scale, ih * scale
            target_rect = QRectF(cx - dw * 0.5, cy - dh * 0.5, dw, dh)
            painter.drawImage(target_rect, img)
        else:
            painter.fillRect(target_rect, QColor("#0D121B"))

        # Subtle inner vignette for optical depth
        vignette = QRadialGradient(QPointF(cx, cy), viewport_r)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.76, QColor(0, 0, 0, 32))
        vignette.setColorAt(1.0, QColor(4, 6, 10, 155))
        painter.setBrush(vignette)
        painter.drawEllipse(QPointF(cx, cy), viewport_r, viewport_r)

        # 3. 3D Geodesic Pose Sphere + 5-Point Facial Constellation
        self._paint_geodesic_sphere(painter, cx, cy, viewport_r * 0.82)
        self._paint_landmark_constellation(painter, target_rect, scale)
        self._paint_pose_reticle(painter, cx, cy, viewport_r)

        # Brief emerald flash wash on burst completion
        if self._capture_flash > 0.02:
            painter.fillRect(
                QRectF(cx - viewport_r, cy - viewport_r, viewport_r * 2.0, viewport_r * 2.0),
                QColor(52, 199, 89, int(55 * self._capture_flash)),
            )
        painter.restore()

        # 4. Optical glass rim around camera circle
        rim_color = (
            QColor(GREEN_HEX)
            if (self._ready and self._aligned) or self._capture_flash > 0.1
            else QColor(255, 255, 255, 58)
        )
        painter.setPen(QPen(rim_color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), viewport_r, viewport_r)

        # 5. Auto-dwell capture arc when aligned & ready
        if self._dwell_progress > 0.02:
            arc_rect = QRectF(
                cx - viewport_r - 4.0,
                cy - viewport_r - 4.0,
                (viewport_r + 4.0) * 2.0,
                (viewport_r + 4.0) * 2.0,
            )
            pen = QPen(QColor(GREEN_HEX), 3.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(arc_rect, 90 * 16, int(-360 * 16 * self._dwell_progress))

        # 6. 64-Tick Apple Face ID Radial Ring
        self._paint_radial_ticks(painter, cx, cy, viewport_r + 10.0)
        painter.end()

    def _paint_geodesic_sphere(self, painter: QPainter, cx: float, cy: float, sphere_r: float) -> None:
        """Render a 3D perspective-projected latitude/longitude geodesic wireframe globe."""
        yaw_rad = self._yaw * 0.68
        pitch_rad = self._pitch * 0.68
        roll_rad = math.radians(self._roll_deg * 0.5)

        wire_color = QColor(GREEN_HEX) if (self._ready and self._aligned) else QColor(CYAN_HEX)
        wire_color.setAlpha(36 if (self._ready and self._aligned) else 26)
        painter.setPen(QPen(wire_color, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Latitude rings (-40, 0, +40 deg) and Longitude meridians (-50, 0, +50 deg)
        for lat_deg in (-38, 0, 38):
            lat = math.radians(lat_deg)
            path = QPainterPath()
            started = False
            for step in range(25):
                lon = -math.pi * 0.48 + (step / 24.0) * (math.pi * 0.96)
                x3, y3, z3 = _project_3d_sphere_point(lat, lon, yaw_rad, pitch_rad, roll_rad)
                if z3 < 0.05:
                    started = False
                    continue
                px = cx + x3 * sphere_r
                py = cy + y3 * sphere_r
                if not started:
                    path.moveTo(px, py)
                    started = True
                else:
                    path.lineTo(px, py)
            painter.drawPath(path)

        for lon_deg in (-42, 0, 42):
            lon = math.radians(lon_deg)
            path = QPainterPath()
            started = False
            for step in range(25):
                lat = -math.pi * 0.45 + (step / 24.0) * (math.pi * 0.90)
                x3, y3, z3 = _project_3d_sphere_point(lat, lon, yaw_rad, pitch_rad, roll_rad)
                if z3 < 0.05:
                    started = False
                    continue
                px = cx + x3 * sphere_r
                py = cy + y3 * sphere_r
                if not started:
                    path.moveTo(px, py)
                    started = True
                else:
                    path.lineTo(px, py)
            painter.drawPath(path)

    def _paint_landmark_constellation(
        self, painter: QPainter, target_rect: QRectF, scale: float
    ) -> None:
        """Render a delicate biometric constellation over the 5 detected facial keypoints."""
        if self._kps is None or self._frame_w <= 0:
            return
        pts: list[QPointF] = []
        for kx, ky in self._kps:
            # Mirror X to match the flipped selfie preview
            mx = (float(self._frame_w) - float(kx)) * scale + target_rect.left()
            my = float(ky) * scale + target_rect.top()
            pts.append(QPointF(mx, my))
        if len(pts) != 5:
            return

        line_color = QColor(GREEN_HEX) if (self._ready and self._aligned) else QColor(CYAN_HEX)
        line_color.setAlpha(95)
        painter.setPen(QPen(line_color, 1.1))
        # Connect left_eye(0)-right_eye(1), eyes to nose(2), nose to mouth corners(3,4), mouth(3)-mouth(4)
        for a, b in ((0, 1), (0, 2), (1, 2), (2, 3), (2, 4), (3, 4)):
            painter.drawLine(pts[a], pts[b])

        dot_color = QColor("#F2F4F8")
        dot_color.setAlpha(210)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        for pt in pts:
            painter.drawEllipse(pt, 2.3, 2.3)

    def _paint_pose_reticle(self, painter: QPainter, cx: float, cy: float, radius: float) -> None:
        tx = cx + self._target_yaw * (radius * 0.52)
        ty = cy + self._target_pitch * (radius * 0.52)
        hx = cx + self._yaw * (radius * 0.52)
        hy = cy + self._pitch * (radius * 0.52)

        guide_pen = QPen(QColor(255, 179, 64, 150), 1.6, Qt.PenStyle.DashLine)
        painter.setPen(guide_pen)
        painter.drawLine(QPointF(hx, hy), QPointF(tx, ty))

        # Directional arrowhead along vector from live head pose to target pose
        dx, dy = tx - hx, ty - hy
        dist = math.hypot(dx, dy)
        if dist > 16.0 and not self._aligned:
            ux, uy = dx / dist, dy / dist
            ax, ay = tx - ux * 14.0, ty - uy * 14.0
            px, py = -uy * 5.0, ux * 5.0
            arrow = QPainterPath()
            arrow.moveTo(ax, ay)
            arrow.lineTo(ax - ux * 8.0 + px, ay - uy * 8.0 + py)
            arrow.lineTo(ax - ux * 8.0 - px, ay - uy * 8.0 - py)
            arrow.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 179, 64, 210))
            painter.drawPath(arrow)

        target_color = QColor(GREEN_HEX) if (self._ready and self._aligned) else QColor(AMBER_HEX)
        painter.setPen(QPen(target_color, 2.0))
        painter.setBrush(QColor(target_color.red(), target_color.green(), target_color.blue(), 42))
        painter.drawEllipse(QPointF(tx, ty), 12.5, 12.5)

        cursor_color = QColor(GREEN_HEX) if (self._ready and self._aligned) else QColor(CYAN_HEX)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(cursor_color)
        painter.drawEllipse(QPointF(hx, hy), 5.5, 5.5)

    def _paint_radial_ticks(self, painter: QPainter, cx: float, cy: float, inner_r: float) -> None:
        for idx in range(_RING_TICKS):
            angle = (idx / _RING_TICKS) * math.tau - math.pi * 0.5
            level = self._tick_levels[idx]
            completed = idx in self._completed_ticks
            tick_len = 8.0 + level * 8.5

            if completed:
                color = QColor(GREEN_HEX)
                width = 2.8
            elif level > 0.35:
                color = QColor(AMBER_HEX)
                color.setAlpha(int(140 + 115 * min(1.0, level)))
                width = 2.4
            else:
                color = QColor(242, 244, 248, 52)
                width = 2.0

            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)

            x1 = cx + math.cos(angle) * inner_r
            y1 = cy + math.sin(angle) * inner_r
            x2 = cx + math.cos(angle) * (inner_r + tick_len)
            y2 = cy + math.sin(angle) * (inner_r + tick_len)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() * 0.5, self.height() * 0.5
        outer_r = min(self.width(), self.height()) * 0.45
        viewport_r = outer_r - 12.0

        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), viewport_r, viewport_r)
        painter.save()
        painter.setClipPath(clip)
        if self._frame_image is not None and not self._frame_image.isNull():
            diameter = viewport_r * 2.0
            scale = max(
                diameter / max(1.0, self._frame_image.width()),
                diameter / max(1.0, self._frame_image.height()),
            )
            draw_w = self._frame_image.width() * scale
            draw_h = self._frame_image.height() * scale
            painter.drawImage(
                QRectF(cx - draw_w / 2, cy - draw_h / 2, draw_w, draw_h),
                self._frame_image,
            )
        else:
            painter.fillRect(
                QRectF(cx - viewport_r, cy - viewport_r, viewport_r * 2, viewport_r * 2),
                QColor("#171E29"),
            )

        guide = QRectF(
            cx - viewport_r * 0.34,
            cy - viewport_r * 0.47,
            viewport_r * 0.68,
            viewport_r * 0.94,
        )
        if self._ready and self._aligned:
            guide_color = QColor(GREEN_SOFT_HEX)
        elif self._has_face:
            guide_color = QColor("#82B6FF")
        else:
            guide_color = QColor(255, 255, 255, 165)
        painter.setPen(QPen(guide_color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(guide)
        painter.restore()

        painter.setPen(QPen(QColor("#DDE3EA"), 1.0))
        painter.drawEllipse(QPointF(cx, cy), viewport_r, viewport_r)

        progress_rect = QRectF(
            cx - outer_r, cy - outer_r, outer_r * 2.0, outer_r * 2.0
        )
        painter.setPen(QPen(QColor("#DDE3EA"), 3.0))
        painter.drawArc(progress_rect, 90 * 16, -360 * 16)
        progress_color = QColor(BLUE_HEX)
        if self._ready and self._aligned or self._capture_flash > 0.15:
            progress_color = QColor(GREEN_DEEP_HEX)
        progress_pen = QPen(progress_color, 3.0)
        progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(progress_pen)
        if self._overall_progress > 0.0:
            painter.drawArc(
                progress_rect, 90 * 16, int(-360 * 16 * self._overall_progress)
            )

        if self._dwell_progress > 0.01:
            dwell_pen = QPen(QColor(GREEN_DEEP_HEX), 2.5)
            dwell_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(dwell_pen)
            painter.drawArc(
                guide.adjusted(-5, -5, 5, 5),
                90 * 16,
                int(-360 * 16 * self._dwell_progress),
            )
        self._paint_direction_arrow(painter, cx, cy, viewport_r)
        painter.end()

    def _paint_direction_arrow(self, painter: QPainter, cx: float, cy: float, radius: float) -> None:
        """Paint one calm directional cue, only when a turn is actually needed."""
        vectors = {
            "left": (-1.0, 0.0),
            "right": (1.0, 0.0),
            "up": (0.0, -1.0),
            "down": (0.0, 1.0),
            "up_left": (-0.72, -0.72),
            "up_right": (0.72, -0.72),
        }
        dx, dy = vectors.get(self._guide_direction, (0.0, 0.0))
        if dx == 0.0 and dy == 0.0:
            return

        origin = QPointF(cx - dx * radius * 0.28, cy - dy * radius * 0.28)
        tip = QPointF(cx + dx * radius * 0.28, cy + dy * radius * 0.28)
        pen = QPen(QColor(BLUE_HEX), 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(origin, tip)

        length = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        head = radius * 0.065
        wing = radius * 0.048
        path = QPainterPath()
        path.moveTo(tip)
        path.lineTo(tip.x() - ux * head + px * wing, tip.y() - uy * head + py * wing)
        path.lineTo(tip.x() - ux * head - px * wing, tip.y() - uy * head - py * wing)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BLUE_HEX))
        painter.drawPath(path)


class EnrollmentStudioWindow(QMainWindow):
    """Apple Face ID-style circular biometric enrollment window."""

    def __init__(
        self,
        *,
        angles: list[str],
        profile: str = "robust",
        samples_per_angle: int = 4,
        on_capture_burst: Optional[Callable[[str, int], list]] = None,
        on_switch_profile: Optional[Callable[[str], list[str]]] = None,
    ) -> None:
        super().__init__()
        self._angles = list(angles)
        self._profile = profile
        self._samples_per_angle = samples_per_angle
        self._on_capture_burst = on_capture_burst
        self._on_switch_profile = on_switch_profile

        self._current_index = 0
        self._captured_samples: list = []
        self._completed = False
        self._aborted = False
        self._abort_reason: Optional[str] = None
        self._abort_requested = False
        self._capture_requested = False
        self._last_ready = False
        self._last_aligned = False
        self._last_status_text = "no face"
        self._pose_history: list[tuple[float, float, float]] = []
        self._aligned_since: Optional[float] = None
        self._burst_in_progress = False
        self._analysis_in_progress = False
        self._worker_pool = QThreadPool(self)
        self._worker_pool.setMaxThreadCount(1)
        self._burst_task: Optional[_CaptureBurstTask] = None
        self._analysis_task: Optional[_LiveAnalysisTask] = None

        self._build_ui()
        self._update_step_copy()

    @property
    def captured_samples(self) -> list:
        return list(self._captured_samples)

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def abort_reason(self) -> Optional[str]:
        return self._abort_reason

    @property
    def capture_in_progress(self) -> bool:
        return self._burst_in_progress

    @property
    def analysis_in_progress(self) -> bool:
        return self._analysis_in_progress

    @property
    def selected_profile(self) -> str:
        return self._profile

    @property
    def active_angles(self) -> list[str]:
        return list(self._angles)

    def current_angle(self) -> str:
        if 0 <= self._current_index < len(self._angles):
            return self._angles[self._current_index]
        return ""

    def _build_ui(self) -> None:
        self.setWindowTitle("MajestyGuard | Face setup")
        self.setMinimumSize(520, 600)
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 640, 800)
        self.resize(min(620, max(520, available.width() - 48)), min(740, max(600, available.height() - 36)))
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #F4F6FA; color: #17212D;"
            " font-family: 'Segoe UI'; }"
            "QLabel#brand { color: #667382; font: 600 10px 'Segoe UI'; letter-spacing: 1px; }"
            "QLabel#title { color: #151D28; font: 600 23px 'Segoe UI'; }"
            "QLabel#subtitle { color: #5E6B79; font: 400 13px 'Segoe UI'; }"
            "QLabel#step { color: #455362; font: 600 12px 'Segoe UI'; }"
            "QLabel#status { color: #3F4B58; font: 500 13px 'Segoe UI'; }"
            "QLabel#privacy { color: #788391; font: 400 10px 'Segoe UI'; }"
            "QLabel#coverage { color: #677484; font: 500 11px 'Segoe UI'; }"
            "QLabel#automation { color: " + BLUE_HEX + "; font: 600 12px 'Segoe UI'; }"
            "QProgressBar { background: #DFE4EB; border: 0; border-radius: 2px; max-height: 4px; }"
            "QProgressBar::chunk { background: " + BLUE_HEX + "; border-radius: 2px; }"
            "QPushButton#primary { background: " + BLUE_HEX + "; color: white; border: 0; border-radius: 9px;"
            " padding: 11px 20px; font: 600 13px 'Segoe UI'; }"
            "QPushButton#primary:hover { background: " + BLUE_HOVER_HEX + "; }"
            "QPushButton#primary:disabled { background: #DCE3ED; color: #8793A2; }"
            "QPushButton#secondary { background: transparent; color: #536171; border: 1px solid #D7DDE5;"
            " border-radius: 9px; padding: 10px 15px; font: 600 12px 'Segoe UI'; }"
            "QPushButton#secondary:hover { background: #E9EDF3; }"
            "QComboBox { background: #FFFFFF; color: #344151; border: 1px solid #D7DDE5;"
            " border-radius: 7px; padding: 5px 9px; font: 500 11px 'Segoe UI'; }"
        )

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 20, 28, 18)
        layout.setSpacing(8)

        top_bar = QHBoxLayout()
        brand = QLabel("MAJESTYGUARD", root)
        brand.setObjectName("brand")
        top_bar.addWidget(brand)
        top_bar.addStretch(1)

        coverage_label = QLabel("Coverage", root)
        coverage_label.setObjectName("coverage")
        top_bar.addWidget(coverage_label)
        self._profile_combo = QComboBox(root)
        self._profile_combo.addItem("Quick", "quick")
        self._profile_combo.addItem("Everyday", "robust")
        self._profile_combo.addItem("Extended", "real-life")
        selected_index = self._profile_combo.findData(self._profile)
        self._profile_combo.setCurrentIndex(max(0, selected_index))
        self._profile_combo.currentIndexChanged.connect(
            lambda _index: self._handle_profile_change(str(self._profile_combo.currentData()))
        )
        top_bar.addWidget(self._profile_combo)
        layout.addLayout(top_bar)

        self._title_label = QLabel("Set up face recognition", root)
        self._title_label.setObjectName("title")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(
            "Follow a few simple directions. We capture each view when you hold still.",
            root,
        )
        self._subtitle_label.setObjectName("subtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._subtitle_label)

        self.canvas = FaceIdRingCanvas(root)
        layout.addWidget(self.canvas, 1)

        progress_row = QHBoxLayout()
        self._step_chip = QLabel("View 1 of 12", root)
        self._step_chip.setObjectName("step")
        progress_row.addWidget(self._step_chip)
        self._progress_bar = QProgressBar(root)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        progress_row.addWidget(self._progress_bar, 1)
        layout.addLayout(progress_row)

        self._status_chip = QLabel("Move into the camera frame.", root)
        self._status_chip.setObjectName("status")
        self._status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_chip)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._cancel_btn = QPushButton("Cancel setup", root)
        self._cancel_btn.setObjectName("secondary")
        self._cancel_btn.clicked.connect(lambda: self.abort("cancel_button"))
        btn_row.addWidget(self._cancel_btn)

        self._capture_btn = QPushButton("Capture this view", root)
        self._capture_btn.setObjectName("primary")
        self._capture_btn.setToolTip("Press Space to capture when the guide is aligned")
        self._capture_btn.clicked.connect(self.trigger_capture)
        self._capture_btn.hide()
        self._automation_label = QLabel("Automatic capture is on", root)
        self._automation_label.setObjectName("automation")
        self._automation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.addWidget(self._automation_label, 1)
        layout.addLayout(btn_row)

        self._privacy_label = QLabel(
            "Video is not saved. Your encrypted face template is written only after setup completes.",
            root,
        )
        self._privacy_label.setObjectName("privacy")
        self._privacy_label.setWordWrap(True)
        self._privacy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._privacy_label)

    def _handle_profile_change(self, profile: str) -> None:
        if self._current_index > 0 or self._on_switch_profile is None:
            return
        new_angles = self._on_switch_profile(profile)
        if new_angles:
            self._profile = profile
            self._angles = list(new_angles)
            self._update_step_copy()

    def _update_step_copy(self) -> None:
        total = max(1, len(self._angles))
        if self._current_index >= total:
            self._title_label.setText("Face setup complete")
            self._subtitle_label.setText("Securing your face template on this device.")
            self._step_chip.setText(f"View {total} of {total}")
            self._progress_bar.setValue(100)
            self._capture_btn.setEnabled(False)
            return

        angle = self._angles[self._current_index]
        title, detail = instruction_for_angle(angle)
        self._title_label.setText(title)
        self._subtitle_label.setText(detail)
        self._step_chip.setText(f"View {self._current_index + 1} of {total}")
        self._progress_bar.setValue(round(self._current_index * 100 / total))
        self._capture_btn.setEnabled(False)
        self._status_chip.setText("Move into the camera frame." if self._current_index == 0 else "Follow the direction above.")

    def update_live_frame(
        self,
        frame: Optional[np.ndarray],
        face: Optional[object],
        *,
        ready: bool,
        status_text: str,
        quality_score: float,
    ) -> None:
        if self._completed or self._aborted or self._burst_in_progress:
            return

        angle = self.current_angle()
        ty, tp, _tol = target_pose_for_angle(angle)
        kps = getattr(face, "kps", None) if face is not None else None
        if kps is not None:
            yaw, pitch, roll_deg = estimate_head_pose_5pt(kps)
            self._pose_history.append((yaw, pitch, roll_deg))
            self._pose_history = self._pose_history[-3:]
            yaw, pitch, roll_deg = np.median(np.asarray(self._pose_history), axis=0).tolist()
            aligned, _dist = check_pose_alignment(yaw, pitch, angle, roll_deg)
        else:
            yaw, pitch, roll_deg = 0.0, 0.0, 0.0
            aligned = False
            self._pose_history.clear()

        self._last_ready = bool(ready)
        self._last_aligned = bool(aligned)
        self._last_status_text = str(status_text)

        now = time.monotonic()
        if ready and aligned:
            if self._aligned_since is None:
                self._aligned_since = now
            dwell = (now - self._aligned_since) / _AUTO_DWELL_SECONDS
        else:
            self._aligned_since = None
            dwell = 0.0

        overall = self._current_index / max(1, len(self._angles))
        self.canvas.set_frame_state(
            frame,
            yaw=yaw,
            pitch=pitch,
            roll_deg=roll_deg,
            kps=kps,
            target_yaw=ty,
            target_pitch=tp,
            ready=ready,
            aligned=aligned,
            dwell_progress=dwell,
            overall_progress=overall,
            guide_direction=guide_direction_for_angle(angle),
        )

        if dwell >= 1.0:
            self._status_chip.setText("Holding steady. Capturing this view…")
        elif ready and not aligned:
            self._status_chip.setText("Follow the direction above.")
        elif ready:
            self._status_chip.setText("Hold still for a moment.")
        else:
            self._status_chip.setText(friendly_readiness_message(status_text))
        self._capture_btn.setEnabled(ready and aligned)

        if dwell >= 1.0:
            self._aligned_since = None
            self.trigger_capture()

    def update_preview_frame(self, frame: np.ndarray) -> None:
        if self._completed or self._aborted or self._burst_in_progress:
            if isinstance(frame, np.ndarray):
                frame.fill(0)
            return
        try:
            self.canvas.set_preview_frame(frame)
        finally:
            frame.fill(0)

    def trigger_capture(self) -> None:
        if self._completed or self._aborted or self._abort_requested or self._burst_in_progress:
            return
        if self._on_capture_burst is None or self._current_index >= len(self._angles):
            return
        if self._analysis_in_progress:
            self._capture_requested = True
            self._status_chip.setText("Checking your position…")
            return
        if not self._last_ready:
            self._status_chip.setText(friendly_readiness_message(self._last_status_text))
            return
        if not self._last_aligned:
            direction, _detail = instruction_for_angle(self.current_angle())
            self._status_chip.setText(f"{direction}.")
            return

        self._burst_in_progress = True
        self._status_chip.setText("Checking this view for a live, clear face…")
        self._capture_btn.setEnabled(False)
        angle = self._angles[self._current_index]
        task = _CaptureBurstTask(self._on_capture_burst, angle, self._current_index + 1)
        task.signals.finished.connect(self._finish_capture_burst)
        self._burst_task = task
        self._worker_pool.start(task)

    def analyze_live_frame_async(
        self,
        frame: np.ndarray,
        analyzer: Callable[[np.ndarray], tuple],
    ) -> None:
        if self._completed or self._aborted or self._abort_requested or self._burst_in_progress:
            frame.fill(0)
            return
        if self._analysis_in_progress:
            frame.fill(0)
            return

        self._analysis_in_progress = True
        self._capture_btn.setEnabled(False)
        task = _LiveAnalysisTask(frame, analyzer)
        task.signals.finished.connect(self._finish_live_analysis)
        self._analysis_task = task
        self._worker_pool.start(task)

    def _finish_live_analysis(self, frame: object, result: object, error: object) -> None:
        self._analysis_in_progress = False
        self._analysis_task = None
        if not isinstance(frame, np.ndarray):
            return

        face = None
        ready = False
        status = "camera check unavailable"
        quality_score = 0.0
        if error is None and isinstance(result, tuple) and len(result) == 4:
            face, ready, status, quality_score = result
        try:
            if self._abort_requested:
                self._discard_captured_samples()
                self._aborted = True
                self.close()
                return

            capture_requested = self._capture_requested
            self._capture_requested = False
            self.update_live_frame(
                None,
                face,
                ready=bool(ready),
                status_text=str(status),
                quality_score=float(quality_score),
            )
            if capture_requested and not self._burst_in_progress:
                if self._capture_btn.isEnabled():
                    self.trigger_capture()
                else:
                    self._status_chip.setText(
                        f"{instruction_for_angle(self.current_angle())[0]}."
                        if self._last_ready
                        else friendly_readiness_message(self._last_status_text)
                    )
        except (TypeError, ValueError):
            self._status_chip.setText("Camera check hiccup. Keeping the camera preview live.")
        finally:
            frame.fill(0)
            embedding = getattr(face, "normed_embedding", None) if face is not None else None
            if isinstance(embedding, np.ndarray):
                embedding.fill(0)

    def _finish_capture_burst(self, burst: object, error: object, status: object = "") -> None:
        self._burst_in_progress = False
        self._burst_task = None
        self._aligned_since = None
        self._last_ready = False
        self._last_aligned = False
        samples = burst if isinstance(burst, list) else []

        if self._abort_requested:
            self._zero_sample_embeddings(samples)
            self._discard_captured_samples()
            self._aborted = True
            self.close()
            return

        if error is not None:
            self._status_chip.setText("Camera check failed. Keep the camera clear and try again.")
            self._capture_btn.setEnabled(False)
            return

        if len(samples) >= self._samples_per_angle:
            self._captured_samples.extend(samples)
            self._current_index += 1
            self._pose_history.clear()
            self._profile_combo.setEnabled(False)
            self.canvas.complete_view(
                self._current_index / max(1, len(self._angles))
            )
            self._update_step_copy()
            self._status_chip.setText(str(status or "View captured."))
            if self._current_index >= len(self._angles):
                self._completed = True
                self.close()
        else:
            self._zero_sample_embeddings(samples)
            self.canvas.clear_tracking()
            self._status_chip.setText(
                str(status or "Could not capture this view. Keep still and try again.")
            )
            self._capture_btn.setEnabled(self._last_ready and self._last_aligned)

    @staticmethod
    def _zero_sample_embeddings(samples: list) -> None:
        for sample in samples:
            embedding = getattr(sample, "embedding", None)
            if isinstance(embedding, np.ndarray):
                embedding.fill(0)

    def _discard_captured_samples(self) -> None:
        self._zero_sample_embeddings(self._captured_samples)
        self._captured_samples.clear()

    def abort(self, reason: str = "user_cancel") -> None:
        """Public abort method used by Cancel button, Escape key, and deadline timer."""
        if self._completed or self._aborted:
            return
        if self._abort_reason is None:
            self._abort_reason = reason
        self._capture_requested = False
        if self._burst_in_progress or self._analysis_in_progress:
            self._abort_requested = True
            self._status_chip.setText("Finishing capture safely…")
            self._capture_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            return
        self._aborted = True
        self._discard_captured_samples()
        self.close()

    def _abort(self) -> None:
        self.abort()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Space:
            self.trigger_capture()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.abort("escape")
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._burst_in_progress or self._analysis_in_progress:
            self.abort("window_closed")
            event.ignore()
            return
        if not self._completed:
            if self._abort_reason is None:
                self._abort_reason = "window_closed"
            self._aborted = True
            self._discard_captured_samples()
        event.accept()
