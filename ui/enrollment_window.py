"""
enrollment_window.py — Apple Face ID-inspired 3D Circular Registration Studio.

Provides a native PyQt6 circular biometric registration interface with:
  - 64-tick radial progress ring with per-sector spring extension, completion wave, and glow
  - Real-time 5-point facial landmark 3D head-pose estimation (yaw, pitch, roll)
  - 3D perspective-projected latitude/longitude geodesic pose sphere & landmark constellation
  - Hybrid auto-dwell capture (360ms hold when aligned & ready) + Spacebar trigger
  - Zero-frame-retention privacy (frames zeroed immediately after QImage copy)
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_RING_TICKS = 64
_AUTO_DWELL_SECONDS = 0.36


def estimate_head_pose_5pt(
    kps: Optional[np.ndarray],
    *,
    mirror_yaw: bool = True,
) -> tuple[float, float, float]:
    """Estimate normalized (yaw, pitch, roll_deg) in [-1.0, 1.0] from 5 InsightFace keypoints.

    Keypoint order: [left_eye, right_eye, nose, left_mouth, right_mouth].
    Returns (0.0, 0.0, 0.0) if keypoints are missing or degenerate.
    """
    if kps is None:
        return 0.0, 0.0, 0.0
    try:
        pts = np.asarray(kps, dtype=np.float32)
        if pts.shape != (5, 2):
            return 0.0, 0.0, 0.0
        l_eye, r_eye, nose, l_mouth, r_mouth = pts
        eye_dx = float(r_eye[0] - l_eye[0])
        eye_dy = float(r_eye[1] - l_eye[1])
        inter_eye = math.hypot(eye_dx, eye_dy)
        if inter_eye < 4.0:
            return 0.0, 0.0, 0.0

        eye_mid_x = float(l_eye[0] + r_eye[0]) * 0.5
        eye_mid_y = float(l_eye[1] + r_eye[1]) * 0.5
        mouth_mid_y = float(l_mouth[1] + r_mouth[1]) * 0.5
        face_vert = max(4.0, mouth_mid_y - eye_mid_y)

        raw_yaw = (float(nose[0]) - eye_mid_x) / (inter_eye * 0.52)
        if mirror_yaw:
            raw_yaw = -raw_yaw

        nose_ratio = (float(nose[1]) - eye_mid_y) / face_vert
        raw_pitch = (nose_ratio - 0.56) / 0.24

        roll_deg = math.degrees(math.atan2(eye_dy, eye_dx))
        yaw = float(np.clip(raw_yaw, -1.0, 1.0))
        pitch = float(np.clip(raw_pitch, -1.0, 1.0))
        return yaw, pitch, float(np.clip(roll_deg, -45.0, 45.0))
    except Exception:
        return 0.0, 0.0, 0.0


def target_pose_for_angle(angle_description: str) -> tuple[float, float, float]:
    """Return (target_yaw, target_pitch, tolerance) in [-1.0, 1.0] for a named enrollment angle."""
    low = (angle_description or "").lower()
    if "left-up" in low:
        return -0.42, -0.35, 0.48
    if "right-up" in low:
        return 0.42, -0.35, 0.48
    if "left 25" in low:
        return -0.58, 0.0, 0.46
    if "right 25" in low:
        return 0.58, 0.0, 0.46
    if "left 10" in low or "slight left" in low or "tilted left" in low:
        return -0.32, 0.0, 0.44
    if "right 10" in low or "slight right" in low or "tilted right" in low:
        return 0.32, 0.0, 0.44
    if "up" in low:
        return 0.0, -0.36, 0.46
    if "down" in low and "lying down" not in low:
        return 0.0, 0.36, 0.46
    return 0.0, 0.0, 0.65


def check_pose_alignment(
    yaw: float,
    pitch: float,
    angle_description: str,
) -> tuple[bool, float]:
    """Return (aligned, normalized_distance) between live (yaw, pitch) and the angle's target."""
    ty, tp, tol = target_pose_for_angle(angle_description)
    dist = math.hypot(yaw - ty, pitch - tp)
    return dist <= tol, float(dist)


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
    """Circular mirrored camera viewport with a 64-tick 3D Face ID ring and geodesic sphere."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self._frame_image: Optional[QImage] = None
        self._frame_w = 640
        self._frame_h = 480
        self._kps: Optional[np.ndarray] = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._roll_deg = 0.0
        self._target_yaw = 0.0
        self._target_pitch = 0.0
        self._ready = False
        self._aligned = False
        self._dwell_progress = 0.0
        self._overall_progress = 0.0
        self._capture_flash = 0.0
        self._phase = 0.0
        self._tick_levels = [0.0] * _RING_TICKS
        self._completed_ticks: set[int] = set()

    def trigger_capture_flash(self) -> None:
        self._capture_flash = 1.0
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
    ) -> None:
        # Smoothly interpolate pose so the 3D globe glides at 60fps
        self._yaw += (float(np.clip(yaw, -1.0, 1.0)) - self._yaw) * 0.42
        self._pitch += (float(np.clip(pitch, -1.0, 1.0)) - self._pitch) * 0.42
        self._roll_deg += (float(np.clip(roll_deg, -45.0, 45.0)) - self._roll_deg) * 0.42
        self._target_yaw = float(np.clip(target_yaw, -1.0, 1.0))
        self._target_pitch = float(np.clip(target_pitch, -1.0, 1.0))
        self._ready = bool(ready)
        self._aligned = bool(aligned)
        self._dwell_progress = float(np.clip(dwell_progress, 0.0, 1.0))
        self._overall_progress = float(np.clip(overall_progress, 0.0, 1.0))
        self._phase = (self._phase + 0.045) % math.tau
        self._capture_flash = max(0.0, self._capture_flash - 0.08)

        if kps is not None:
            try:
                arr = np.asarray(kps, dtype=np.float32)
                self._kps = arr.copy() if arr.shape == (5, 2) else None
            except Exception:
                self._kps = None
        else:
            self._kps = None

        completed_count = int(round(self._overall_progress * _RING_TICKS))
        self._completed_ticks = set(range(completed_count))

        head_angle = math.atan2(self._pitch, self._yaw) % math.tau
        head_mag = min(1.0, math.hypot(self._yaw, self._pitch))
        target_angle = math.atan2(self._target_pitch, self._target_yaw) % math.tau
        target_mag = min(1.0, math.hypot(self._target_yaw, self._target_pitch))

        for idx in range(_RING_TICKS):
            tick_angle = (idx / _RING_TICKS) * math.tau
            target_level = 1.0 if idx in self._completed_ticks else 0.14
            if head_mag > 0.10:
                diff = abs((tick_angle - head_angle + math.pi) % math.tau - math.pi)
                if diff < 0.55:
                    target_level = max(target_level, (1.0 - diff / 0.55) * 0.88)
            if target_mag > 0.10:
                tdiff = abs((tick_angle - target_angle + math.pi) % math.tau - math.pi)
                if tdiff < 0.45:
                    pulse = 0.55 + 0.35 * math.sin(self._phase * 2.2)
                    target_level = max(target_level, (1.0 - tdiff / 0.45) * pulse)
            self._tick_levels[idx] += (target_level - self._tick_levels[idx]) * 0.34

        if bgr_frame is not None and bgr_frame.size > 0:
            self._frame_h, self._frame_w = bgr_frame.shape[:2]
            mirrored = cv2.flip(bgr_frame, 1)
            rgb = cv2.cvtColor(mirrored, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            self._frame_image = QImage(
                rgb.data, w, h, ch * w, QImage.Format.Format_RGB888
            ).copy()
            rgb[:] = 0
            mirrored[:] = 0
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
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
            QColor("#34C759")
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
            pen = QPen(QColor("#34C759"), 3.0)
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

        wire_color = QColor("#34C759") if (self._ready and self._aligned) else QColor("#64D2FF")
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

        line_color = QColor("#34C759") if (self._ready and self._aligned) else QColor("#64D2FF")
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

        target_color = QColor("#34C759") if (self._ready and self._aligned) else QColor("#FFB340")
        painter.setPen(QPen(target_color, 2.0))
        painter.setBrush(QColor(target_color.red(), target_color.green(), target_color.blue(), 42))
        painter.drawEllipse(QPointF(tx, ty), 12.5, 12.5)

        cursor_color = QColor("#34C759") if (self._ready and self._aligned) else QColor("#64D2FF")
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
                color = QColor("#34C759")
                width = 2.8
            elif level > 0.35:
                color = QColor("#FFB340")
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
        self._aligned_since: Optional[float] = None
        self._burst_in_progress = False

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
        self.setWindowTitle("MajestyGuard — Face ID Studio")
        self.setMinimumSize(560, 680)
        self.resize(580, 720)
        self.setStyleSheet(
            "QMainWindow { background: #080B11; color: #F2F4F8; }"
            "QLabel#eyebrow { color: #64D2FF; font: 600 10px 'Segoe UI Variable Text'; letter-spacing: 1.5px; }"
            "QLabel#title { color: #F2F4F8; font: 600 24px 'Segoe UI Variable Display'; }"
            "QLabel#subtitle { color: #8A96A8; font: 400 13px 'Segoe UI Variable Text'; }"
            "QLabel#chip { background: #111723; color: #D5DCE6; border: 1px solid #1E293B;"
            " border-radius: 11px; padding: 5px 12px; font: 600 11px 'Segoe UI Variable Text'; }"
            "QPushButton#primary { background: #34C759; color: #061209; border: 0; border-radius: 10px;"
            " padding: 12px 22px; font: 600 13px 'Segoe UI Variable Display'; }"
            "QPushButton#primary:hover { background: #3CD863; }"
            "QPushButton#primary:disabled { background: #182232; color: #586577; }"
            "QPushButton#secondary { background: #121824; color: #C3CCD9; border: 1px solid #222E42;"
            " border-radius: 10px; padding: 10px 16px; font: 600 12px 'Segoe UI Variable Text'; }"
            "QComboBox { background: #111723; color: #F2F4F8; border: 1px solid #1E293B;"
            " border-radius: 8px; padding: 5px 10px; font: 600 11px 'Segoe UI Variable Text'; }"
        )

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        eyebrow = QLabel("BIOMETRIC ENROLLMENT STUDIO", root)
        eyebrow.setObjectName("eyebrow")
        top_bar.addWidget(eyebrow)
        top_bar.addStretch(1)

        self._profile_combo = QComboBox(root)
        self._profile_combo.addItems(["robust", "real-life", "quick"])
        self._profile_combo.setCurrentText(self._profile)
        self._profile_combo.currentTextChanged.connect(self._handle_profile_change)
        top_bar.addWidget(self._profile_combo)
        layout.addLayout(top_bar)

        self._title_label = QLabel("Position your face in the circle", root)
        self._title_label.setObjectName("title")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(
            "Move your head slowly to complete the circle, or press SPACE when ready.",
            root,
        )
        self._subtitle_label.setObjectName("subtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._subtitle_label)

        self.canvas = FaceIdRingCanvas(root)
        layout.addWidget(self.canvas, 1)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._step_chip = QLabel("Pose 1 / 12", root)
        self._step_chip.setObjectName("chip")
        self._quality_chip = QLabel("Quality: --", root)
        self._quality_chip.setObjectName("chip")
        self._status_chip = QLabel("Waiting for face", root)
        self._status_chip.setObjectName("chip")
        chips_row.addStretch(1)
        chips_row.addWidget(self._step_chip)
        chips_row.addWidget(self._quality_chip)
        chips_row.addWidget(self._status_chip)
        chips_row.addStretch(1)
        layout.addLayout(chips_row)

        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._cancel_btn = QPushButton("Cancel", root)
        self._cancel_btn.setObjectName("secondary")
        self._cancel_btn.clicked.connect(self.abort)
        btn_row.addWidget(self._cancel_btn)

        self._capture_btn = QPushButton("Capture Pose (SPACE)", root)
        self._capture_btn.setObjectName("primary")
        self._capture_btn.clicked.connect(self.trigger_capture)
        btn_row.addWidget(self._capture_btn, 1)
        layout.addLayout(btn_row)

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
            self._title_label.setText("Face ID Gallery Complete")
            self._subtitle_label.setText("Encrypting biometric templates with Windows DPAPI...")
            self._step_chip.setText(f"Done {total} / {total}")
            self._capture_btn.setEnabled(False)
            return

        angle = self._angles[self._current_index]
        self._title_label.setText(angle.split(" - ")[0].strip())
        detail = angle.split(" - ")[1].strip() if " - " in angle else angle
        self._subtitle_label.setText(
            f"{detail}  ·  Hold steady in the target ring or press SPACE."
        )
        self._step_chip.setText(f"Pose {self._current_index + 1} / {total}")

    def update_live_frame(
        self,
        frame: np.ndarray,
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
            aligned, _dist = check_pose_alignment(yaw, pitch, angle)
        else:
            yaw, pitch, roll_deg = 0.0, 0.0, 0.0
            aligned = False

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
        )

        self._quality_chip.setText(f"Quality: {quality_score * 100:.0f}%")
        if dwell >= 1.0:
            self._status_chip.setText("LOCKED ON — Capturing...")
        elif ready and not aligned:
            self._status_chip.setText("ALIGN HEAD WITH TARGET RING")
        else:
            self._status_chip.setText(status_text.upper())
        self._capture_btn.setEnabled(ready and aligned)

        if dwell >= 1.0:
            self._aligned_since = None
            self.trigger_capture()

    def trigger_capture(self) -> None:
        if self._completed or self._aborted or self._burst_in_progress:
            return
        if self._on_capture_burst is None or self._current_index >= len(self._angles):
            return

        self._burst_in_progress = True
        self._status_chip.setText("CAPTURING BURST...")
        self.repaint()
        try:
            angle = self._angles[self._current_index]
            burst = self._on_capture_burst(angle, self._current_index + 1)
            if len(burst) >= self._samples_per_angle:
                self._captured_samples.extend(burst)
                self._current_index += 1
                self._profile_combo.setEnabled(False)
                self.canvas.trigger_capture_flash()
                self._update_step_copy()
                if self._current_index >= len(self._angles):
                    self._completed = True
                    self.close()
            else:
                self._status_chip.setText(
                    f"Captured {len(burst)}/{self._samples_per_angle} — hold steady & retry"
                )
        finally:
            self._burst_in_progress = False
            self._aligned_since = None

    def abort(self) -> None:
        """Public abort method used by Cancel button, Escape key, and deadline timer."""
        self._aborted = True
        self.close()

    def _abort(self) -> None:
        self.abort()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Space:
            self.trigger_capture()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.abort()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._completed:
            self._aborted = True
        event.accept()
