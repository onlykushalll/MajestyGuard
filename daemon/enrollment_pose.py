"""Shared pose estimation and target matching for guided enrollment."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


def _needs_directional_pose_check(angle_description: str) -> bool:
    """Return whether a view needs a reliable left/right orientation cue.

    Five-point landmarks give a useful horizontal turn signal on ordinary
    webcams. Their vertical estimate is much less stable across camera height,
    focal length, and posture, so baseline and real-life coverage views are
    deliberately gated by the full quality/liveness pipeline rather than a
    brittle pseudo-3D pitch threshold.
    """
    low = (angle_description or "").lower()
    return any(
        marker in low
        for marker in ("left 10", "right 10", "slight left", "slight right", "left 25", "right 25", "left-up", "right-up")
    )


def estimate_head_pose_5pt(
    kps: Optional[np.ndarray],
    *,
    mirror_yaw: bool = True,
) -> tuple[float, float, float]:
    """Return normalized yaw/pitch and roll degrees from InsightFace's five landmarks."""
    if kps is None:
        return 0.0, 0.0, 0.0
    try:
        pts = np.asarray(kps, dtype=np.float32)
        if pts.shape != (5, 2) or not np.all(np.isfinite(pts)):
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

        yaw = (float(nose[0]) - eye_mid_x) / (inter_eye * 0.52)
        if mirror_yaw:
            yaw = -yaw
        nose_ratio = (float(nose[1]) - eye_mid_y) / face_vert
        pitch = (nose_ratio - 0.56) / 0.24
        roll = math.degrees(math.atan2(eye_dy, eye_dx))
        return (
            float(np.clip(yaw, -1.0, 1.0)),
            float(np.clip(pitch, -1.0, 1.0)),
            float(np.clip(roll, -45.0, 45.0)),
        )
    except Exception:
        return 0.0, 0.0, 0.0


def target_pose_for_angle(angle_description: str) -> tuple[float, float, float]:
    """Return a practical target pose and broad capture tolerance for a guided view."""
    low = (angle_description or "").lower()
    if "left-up" in low:
        return -0.42, -0.35, 0.42
    if "right-up" in low:
        return 0.42, -0.35, 0.42
    if "tilted left" in low or "tilt left" in low:
        return 0.0, 0.0, 0.30
    if "tilted right" in low or "tilt right" in low:
        return 0.0, 0.0, 0.30
    if "left 25" in low:
        return -0.52, 0.0, 0.36
    if "right 25" in low:
        return 0.52, 0.0, 0.36
    if "left 10" in low or "slight left" in low:
        return -0.30, 0.0, 0.30
    if "right 10" in low or "slight right" in low:
        return 0.30, 0.0, 0.30
    if "up" in low:
        return 0.0, -0.30, 0.34
    if "down" in low and "lying down" not in low:
        return 0.0, 0.30, 0.34
    return 0.0, 0.0, 0.30


def check_pose_alignment(
    yaw: float,
    pitch: float,
    angle_description: str,
    roll_deg: float = 0.0,
) -> tuple[bool, float]:
    """Check the requested yaw/pitch target, including roll for pillow-tilt views."""
    if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(roll_deg)):
        return False, 0.0
    if not _needs_directional_pose_check(angle_description):
        return True, 0.0

    ty, tp, tolerance = target_pose_for_angle(angle_description)
    distance = math.hypot(yaw - ty, pitch - tp)
    aligned = distance <= tolerance

    low = (angle_description or "").lower()
    if "left-up" in low:
        aligned = aligned and yaw <= -0.15 and pitch <= -0.10
    elif "right-up" in low:
        aligned = aligned and yaw >= 0.15 and pitch <= -0.10
    elif "left 25" in low:
        aligned = aligned and yaw <= -0.24
    elif "right 25" in low:
        aligned = aligned and yaw >= 0.24
    elif "left 10" in low or "slight left" in low:
        aligned = aligned and yaw <= -0.12
    elif "right 10" in low or "slight right" in low:
        aligned = aligned and yaw >= 0.12
    elif "up" in low:
        aligned = aligned and pitch <= -0.10
    elif "down" in low and "lying down" not in low:
        aligned = aligned and pitch >= 0.10
    elif "tilted left" in low or "tilt left" in low:
        aligned = aligned and 6.0 <= roll_deg <= 30.0
    elif "tilted right" in low or "tilt right" in low:
        aligned = aligned and -30.0 <= roll_deg <= -6.0
    return aligned, float(distance)


def enrollment_pose_aligned(kps: Optional[np.ndarray], angle_description: str) -> bool:
    if kps is None:
        return False
    yaw, pitch, roll = estimate_head_pose_5pt(kps)
    aligned, _distance = check_pose_alignment(yaw, pitch, angle_description, roll)
    return aligned


def instruction_for_angle(angle_description: str) -> tuple[str, str]:
    """Return a short user-facing direction and supporting hold-still copy."""
    low = (angle_description or "").lower()
    if "left-up" in low:
        return "Turn slightly left and lift your chin", "Keep your face inside the guide."
    if "right-up" in low:
        return "Turn slightly right and lift your chin", "Keep your face inside the guide."
    if "tilted left" in low or "tilt left" in low:
        return "Tilt your head gently to your left", "Keep your face toward the camera."
    if "tilted right" in low or "tilt right" in low:
        return "Tilt your head gently to your right", "Keep your face toward the camera."
    if "left 25" in low:
        return "Turn a little farther to your left", "Follow the guide, then hold still."
    if "right 25" in low:
        return "Turn a little farther to your right", "Follow the guide, then hold still."
    if "left 10" in low or "slight left" in low:
        return "Turn slightly to your left", "Follow the guide, then hold still."
    if "right 10" in low or "slight right" in low:
        return "Turn slightly to your right", "Follow the guide, then hold still."
    if "chin slightly up" in low or "look up" in low:
        return "Lift your chin slightly", "Keep both eyes visible."
    if "chin slightly down" in low or "look down" in low:
        return "Lower your chin slightly", "Keep both eyes visible."
    if "closer" in low or "close relaxed" in low:
        return "Move a little closer", "Keep your whole face inside the guide."
    if "leaning back" in low or "away" in low:
        return "Lean back naturally", "Keep your face comfortably visible."
    if "lying down" in low:
        return "Settle into your resting position", "Look toward the camera and hold still."
    if "glasses" in low:
        return "Use your usual posture", "Keep your glasses on if you wear them."
    if "screen-lit" in low:
        return "Use your usual screen lighting", "Face the camera and hold still."
    if "shadowed" in low:
        return "Use your usual room lighting", "Keep your face visible and hold still."
    if "smile" in low or "expression" in low:
        return "Look into the camera", "Use your natural expression."
    return "Look into the camera", "Keep your face inside the guide."


def guide_direction_for_angle(angle_description: str) -> str:
    """Return a compact visual direction for the guided camera ring."""
    low = (angle_description or "").lower()
    if "left-up" in low:
        return "up_left"
    if "right-up" in low:
        return "up_right"
    if "left 25" in low or "left 10" in low or "slight left" in low:
        return "left"
    if "right 25" in low or "right 10" in low or "slight right" in low:
        return "right"
    if "chin slightly up" in low or "look up" in low:
        return "up"
    if "chin slightly down" in low or "look down" in low:
        return "down"
    return ""


def friendly_readiness_message(status: str) -> str:
    value = (status or "").lower()
    if "camera check unavailable" in value:
        return "Camera check paused briefly. Keep still while it retries."
    if "one face only" in value:
        return "Only one person can enroll at a time."
    if "no face" in value:
        return "Move into the camera frame."
    if "det too low" in value:
        return "Face the camera and move a little closer."
    if "move closer" in value:
        return "Move a little closer to the camera."
    if "move back" in value:
        return "Move back until your full face is visible."
    if "center your face" in value:
        return "Center your face in the guide."
    if "quality low" in value:
        return "Use even light on your face and hold still."
    if "embedding not ready" in value:
        return "Hold still while the camera focuses."
    if "motion blur" in value or "hold still" in value:
        return "Hold still for a moment."
    return "Keep your face visible and hold still."
