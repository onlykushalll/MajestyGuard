"""
presence.py — Frame-diff motion pre-filter + YuNet face detector.

Two responsibilities:
  1. MotionFilter  — skip full CV pipeline when nothing moves in frame
  2. PresenceDetector — YuNet face detector: is *any* face in the frame?

The daemon uses these as cheap gatekeepers before running the expensive
InsightFace + 12-layer liveness pipeline.

YuNet model: models/face_detection_yunet_2023mar.onnx (75K params, <10ms)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution — models/ sits one level above daemon/
# ---------------------------------------------------------------------------
_DAEMON_DIR = Path(__file__).resolve().parent
_MODELS_DIR = _DAEMON_DIR.parent / "models"
_YUNET_MODEL = _MODELS_DIR / "face_detection_yunet_2023mar.onnx"


def _yunet_path() -> Path:
    if _YUNET_MODEL.exists():
        return _YUNET_MODEL
    raise FileNotFoundError(
        f"YuNet model not found at {_YUNET_MODEL}. "
        "Run 'copy' from models/ or re-run the setup."
    )


# ---------------------------------------------------------------------------
# Motion pre-filter
# ---------------------------------------------------------------------------

class MotionFilter:
    """
    Frame-difference pre-filter. Returns True if there's enough motion
    (pixel delta) to justify running the full CV pipeline.

    At 15 FPS with a static empty desk, this typically skips ~95% of frames,
    saving CPU for when it actually matters.
    """

    def __init__(
        self,
        threshold: float = 25.0,   # mean absolute pixel delta to trigger
        min_changed_frac: float = 0.002,  # fraction of pixels that must change
        resize_to: tuple[int, int] = (160, 90),  # downscale for speed
    ):
        self.threshold = threshold
        self.min_changed_frac = min_changed_frac
        self.resize_to = resize_to
        self._prev: Optional[np.ndarray] = None

    def has_motion(self, frame: np.ndarray) -> bool:
        """Return True if this frame shows meaningful motion vs. the previous."""
        # Downscale and greyscale
        small = cv2.resize(frame, self.resize_to, interpolation=cv2.INTER_AREA)
        grey  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if self._prev is None:
            self._prev = grey
            return True  # treat first frame as motion so we do at least one pass

        diff = cv2.absdiff(grey, self._prev)
        self._prev = grey

        changed = np.count_nonzero(diff > self.threshold)
        changed_frac = changed / diff.size
        return changed_frac >= self.min_changed_frac

    def reset(self) -> None:
        self._prev = None


# ---------------------------------------------------------------------------
# YuNet face presence detector
# ---------------------------------------------------------------------------

class PresenceDetector:
    """
    Lightweight YuNet-backed face detector.
    Reports whether *any* face (not necessarily enrolled) is in the frame.
    Used by the daemon state machine for:
      - Social lock: a non-enrolled face triggers immediate lock
      - Idle→Scanning transition: only spin up heavy pipeline when a face appears
    """

    def __init__(
        self,
        score_threshold: float = 0.70,
        nms_threshold: float = 0.30,
        top_k: int = 10,
    ):
        self.score_threshold = score_threshold
        self.nms_threshold   = nms_threshold
        self.top_k           = top_k
        self._det: Optional[cv2.FaceDetectorYN] = None
        self._size: Optional[tuple[int, int]] = None

    def _ensure(self, w: int, h: int) -> cv2.FaceDetectorYN:
        if self._det is None:
            self._det = cv2.FaceDetectorYN.create(
                str(_yunet_path()),
                "",
                (w, h),
                self.score_threshold,
                self.nms_threshold,
                self.top_k,
            )
            self._size = (w, h)
            log.info("YuNet detector initialised (%dx%d)", w, h)
        elif self._size != (w, h):
            self._det.setInputSize((w, h))
            self._size = (w, h)
        return self._det

    def detect(self, frame: np.ndarray) -> list[np.ndarray]:
        """
        Return list of face boxes. Each box is a numpy array:
          [x, y, w, h, right_eye_x, right_eye_y, left_eye_x, left_eye_y,
           nose_x, nose_y, right_mouth_x, right_mouth_y, left_mouth_x,
           left_mouth_y, score]
        Empty list = no faces detected.
        """
        h, w = frame.shape[:2]
        det = self._ensure(w, h)
        _, faces = det.detect(frame)
        if faces is None:
            return []
        primary = self._select_primary_face(frame, list(faces))
        return [primary] if primary is not None else []

    def has_face(self, frame: np.ndarray) -> bool:
        return len(self.detect(frame)) > 0

    def face_count(self, frame: np.ndarray) -> int:
        return len(self.detect(frame))

    @staticmethod
    def _select_primary_face(frame: np.ndarray, faces: list[np.ndarray]) -> Optional[np.ndarray]:
        """Select one centered, high-confidence YuNet detection."""
        if not faces:
            return None

        h, w = frame.shape[:2]
        frame_area = max(1, h * w)
        frame_center_x = w / 2.0
        frame_center_y = h / 2.0

        def score(face: np.ndarray) -> float:
            if len(face) < 15:
                return -1.0

            x, y, box_w, box_h = [float(v) for v in face[:4]]
            if box_w <= 0.0 or box_h <= 0.0:
                return -1.0

            x2 = x + box_w
            y2 = y + box_h
            det_score = float(face[14])
            area_score = min(1.0, ((box_w * box_h) / frame_area) / 0.35)
            face_center_x = x + box_w / 2.0
            face_center_y = y + box_h / 2.0
            dist_x = abs(face_center_x - frame_center_x) / max(1.0, frame_center_x)
            dist_y = abs(face_center_y - frame_center_y) / max(1.0, frame_center_y)
            center_score = max(0.0, 1.0 - ((dist_x + dist_y) / 2.0))
            contains_center = x <= frame_center_x <= x2 and y <= frame_center_y <= y2
            center_bonus = 0.15 if contains_center else 0.0

            return (
                det_score * 0.45 +
                area_score * 0.30 +
                center_score * 0.20 +
                center_bonus
            )

        selected = max(faces, key=score)
        return selected if score(selected) >= 0.0 else None
