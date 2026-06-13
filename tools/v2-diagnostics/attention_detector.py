"""
attention_detector.py — MediaPipe iris gaze VARIABILITY detector.

PURPOSE: Detect a live person vs a static photo/video.
  - Static photo: iris position never changes → std_dev ≈ 0.0 → LOW score
  - Real person working: eyes shift constantly while reading → std_dev > 0.01 → HIGH score

WHAT WE DO NOT DO:
  - We do NOT penalise looking away from the camera.
  - The user is sitting at their computer looking at the screen, not the lens.
    Penalising this would make the system unusable in normal working conditions.
  - The ONNX MiniFASNet + other 10 layers handle photo/video spoof detection.
    This layer adds the specific signal: are these eyes MOVING over time?

SECONDARY SIGNAL: blink detection via Eye Aspect Ratio (EAR).
  Real faces blink every 3-5 seconds. Static photos never blink.
"""
import cv2
import numpy as np
import logging
from collections import deque

logger = logging.getLogger("MajestyGuard.Attention")

# MediaPipe landmark indices
_LEFT_IRIS   = [468, 469, 470, 471]
_RIGHT_IRIS  = [472, 473, 474, 475]
_L_EAR_V     = [(159, 145), (158, 153)]
_L_EAR_H     = (33, 133)
_R_EAR_V     = [(386, 374), (385, 380)]
_R_EAR_H     = (362, 263)

# Gaze variability thresholds (in normalised eye-width units)
_MIN_STD_LIVE   = 0.008   # real person working: std > this (eyes shift while reading)
_SPOOF_STD_MAX  = 0.002   # static photo: std below this
_HISTORY_LEN    = 45      # frames of iris history (~3 seconds at 15 FPS)

# EAR blink threshold
_EAR_BLINK_THRESH = 0.18


class AttentionDetector:
    def __init__(self):
        self._mesh   = None
        self._ready  = False

        # Iris position history — (x, y) normalised per-eye
        self._iris_x_hist: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._iris_y_hist: deque[float] = deque(maxlen=_HISTORY_LEN)

        # Blink tracking
        self._blink_count    = 0
        self._was_blinking   = False
        self._frames_watched = 0

        try:
            import mediapipe as mp
            self._mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,        # enables iris landmarks 468-477
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._ready = True
            logger.info("AttentionDetector ready (gaze variability mode)")
        except Exception as e:
            logger.warning("MediaPipe unavailable — attention layer disabled: %s", e)

    # ── Public API ────────────────────────────────────────────────────

    def score(self, frame: np.ndarray) -> float:
        """
        Returns 0.0–1.0.
          ≥ 0.70 → live person (eyes moving and/or blinking)
          ≤ 0.35 → suspicious (eyes never move — possible static photo)
          0.5    → neutral (not enough history yet, or MediaPipe unavailable)
        """
        if not self._ready or self._mesh is None:
            return 0.5   # neutral — don't penalise if MediaPipe is unavailable

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self._mesh.process(rgb)

        if not result.multi_face_landmarks:
            return 0.5   # no face — neutral, not a penalty

        lm = result.multi_face_landmarks[0].landmark
        self._frames_watched += 1

        # ── Iris position (normalised to eye width) ───────────────────
        iris_x, iris_y = self._iris_position(lm)
        if iris_x is not None:
            self._iris_x_hist.append(iris_x)
            self._iris_y_hist.append(iris_y)

        # ── EAR blink detection ───────────────────────────────────────
        ear = self._ear(lm)
        is_blinking = ear < _EAR_BLINK_THRESH
        if is_blinking and not self._was_blinking:
            self._blink_count += 1
        self._was_blinking = is_blinking

        # ── Score computation ─────────────────────────────────────────
        return self._compute_score()

    # ── Internal ──────────────────────────────────────────────────────

    def _iris_position(self, lm):
        """
        Compute mean iris position normalised by eye width.
        Returns (x, y) or (None, None) if landmarks missing.
        """
        try:
            # Left iris centre
            l_iris_x = np.mean([lm[k].x for k in _LEFT_IRIS])
            l_iris_y = np.mean([lm[k].y for k in _LEFT_IRIS])
            l_eye_w  = abs(lm[_L_EAR_H[1]].x - lm[_L_EAR_H[0]].x) + 1e-6

            # Right iris centre
            r_iris_x = np.mean([lm[k].x for k in _RIGHT_IRIS])
            r_iris_y = np.mean([lm[k].y for k in _RIGHT_IRIS])
            r_eye_w  = abs(lm[_R_EAR_H[1]].x - lm[_R_EAR_H[0]].x) + 1e-6

            # Normalise by eye width so position is scale-invariant
            x = (l_iris_x / l_eye_w + r_iris_x / r_eye_w) / 2
            y = (l_iris_y / l_eye_w + r_iris_y / r_eye_w) / 2
            return x, y
        except Exception:
            return None, None

    def _ear(self, lm) -> float:
        """Eye Aspect Ratio — averaged left + right."""
        def _single(v_pairs, h_pair):
            v_sum = sum(
                abs(lm[a].y - lm[b].y)
                for a, b in v_pairs
            )
            h = abs(lm[h_pair[0]].x - lm[h_pair[1]].x) + 1e-6
            return v_sum / (len(v_pairs) * h)

        l = _single(_L_EAR_V, _L_EAR_H)
        r = _single(_R_EAR_V, _R_EAR_H)
        return (l + r) / 2

    def _compute_score(self) -> float:
        n = len(self._iris_x_hist)

        # ── Not enough frames yet — return neutral ────────────────────
        if n < 8:
            return 0.5

        # ── Gaze variability score ────────────────────────────────────
        # Standard deviation of iris position across recent frames.
        # A real person's gaze wanders; a photo's iris is perfectly still.
        xs = np.array(self._iris_x_hist)
        ys = np.array(self._iris_y_hist)
        std = float(np.mean([np.std(xs), np.std(ys)]))

        if std >= _MIN_STD_LIVE:
            variability_score = 1.0
        elif std <= _SPOOF_STD_MAX:
            variability_score = 0.2   # completely still eyes → likely photo
        else:
            # Linear interpolation between spoof and live thresholds
            variability_score = 0.2 + 0.8 * (
                (std - _SPOOF_STD_MAX) / (_MIN_STD_LIVE - _SPOOF_STD_MAX)
            )

        # ── Blink score ────────────────────────────────────────────────
        # Expected: ~1 blink per 5s = ~3 blinks per 45 frames.
        # Zero blinks in 45 frames is suspicious. Many blinks is fine.
        # Only meaningful after 45 frames of observation.
        if self._frames_watched >= _HISTORY_LEN:
            blink_rate = self._blink_count / (self._frames_watched / _HISTORY_LEN)
            if blink_rate >= 1:
                blink_score = 1.0        # at least 1 blink per observation window
            elif blink_rate > 0:
                blink_score = 0.5 + 0.5 * blink_rate
            else:
                blink_score = 0.4        # no blinks yet — mild suspicion
        else:
            blink_score = 0.5            # not enough frames — neutral

        # ── Combined: variability is primary, blink is supplementary ──
        final = variability_score * 0.70 + blink_score * 0.30

        logger.debug(
            "Attention: std=%.4f var_score=%.3f blinks=%d blink_score=%.3f → %.3f",
            std, variability_score, self._blink_count, blink_score, final
        )
        return float(np.clip(final, 0.0, 1.0))

    def reset(self) -> None:
        self._iris_x_hist.clear()
        self._iris_y_hist.clear()
        self._blink_count    = 0
        self._was_blinking   = False
        self._frames_watched = 0

    def close(self) -> None:
        """Release MediaPipe resources."""
        if self._mesh is not None:
            try:
                self._mesh.close()
            except Exception:
                pass
            self._mesh  = None
            self._ready = False
