"""
Shared helpers for camera diagnostics and enrollment.

These functions mirror daemon-side preprocessing and primary-face selection
without opening a camera or touching machine state.
"""
from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np

_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def posture_label(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "unspecified"
    label = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return label or "unspecified"


def enhance_frame(frame: np.ndarray) -> np.ndarray:
    """Mirror FaceEngine._enhance_frame for diagnostic parity."""
    try:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        mean_l = float(np.mean(l))
        if mean_l > 100:
            return frame
        l_enhanced = _CLAHE.apply(l)
        if mean_l < 50:
            gamma = 0.6
            l_enhanced = (np.power(l_enhanced / 255.0, gamma) * 255.0).astype(np.uint8)
        enhanced = cv2.merge([l_enhanced, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    except (cv2.error, ValueError, TypeError):
        return frame


def select_primary_face(frame: np.ndarray, faces: list) -> Optional[object]:
    if not faces:
        return None

    h, w = frame.shape[:2]
    frame_area = max(1, h * w)
    frame_center_x = w / 2.0
    frame_center_y = h / 2.0

    def score(face) -> float:
        try:
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
        except (AttributeError, TypeError, ValueError):
            return -1.0

        box_w = max(0.0, x2 - x1)
        box_h = max(0.0, y2 - y1)
        if box_w <= 0.0 or box_h <= 0.0:
            return -1.0

        area_score = min(1.0, ((box_w * box_h) / frame_area) / 0.35)
        face_center_x = (x1 + x2) / 2.0
        face_center_y = (y1 + y2) / 2.0
        dist_x = abs(face_center_x - frame_center_x) / max(1.0, frame_center_x)
        dist_y = abs(face_center_y - frame_center_y) / max(1.0, frame_center_y)
        center_score = max(0.0, 1.0 - ((dist_x + dist_y) / 2.0))
        contains_center = x1 <= frame_center_x <= x2 and y1 <= frame_center_y <= y2
        center_bonus = 0.15 if contains_center else 0.0
        det_score = float(getattr(face, "det_score", 0.75))
        return det_score * 0.45 + area_score * 0.30 + center_score * 0.20 + center_bonus

    selected = max(faces, key=score)
    return selected if score(selected) >= 0.0 else None
