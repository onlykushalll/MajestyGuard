# MajestyGuard.CVEngine/enrollment.py
# One-time enrollment flow.
# Captures face embeddings from multiple angles and returns them
# to the Service for DPAPI-encrypted storage.
#
# FLOW:
#   1. Service sends EnrollFrameMsg with angle name
#   2. cv_server.py calls FaceEngine.capture_enrollment_frame()
#   3. Returns 512-dim embedding via pipe as EnrollResult JSON
#   4. Service (EmbeddingStore) encrypts and saves all embeddings
#
# QUALITY REQUIREMENTS (enforced here):
#   - Only one face visible during enrollment
#   - Face must pass liveness check (no photo enrollment)
#   - Face must be within acceptable size range (not too far/close)
#   - Min confidence: detection score ≥ 0.85
#   - Minimum 3 angles required before enrollment is accepted
#
# CODEX: Wire this into the enrollment UI (a separate WinForms/WinUI
#        window shown during first-run setup, NOT on Secure Desktop).

import os
import time
import json
import logging
import numpy as np
import cv2
from dataclasses import dataclass, field, asdict
from typing import Optional
from face_engine import FaceEngine

logger = logging.getLogger("MajestyGuard.Enrollment")

REQUIRED_ANGLES = ["Front", "SlightLeft", "SlightRight"]
OPTIONAL_ANGLES = ["LookUp", "LookDown", "WithGlasses"]


@dataclass
class EnrollmentSession:
    user_sid: str
    captured_angles: dict[str, list[float]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return all(a in self.captured_angles for a in REQUIRED_ANGLES)

    @property
    def embedding_count(self) -> int:
        return len(self.captured_angles)


class EnrollmentManager:
    """
    Manages the enrollment session state.
    Called by cv_server.py in response to EnrollFrame commands.
    """

    # Min/max face bounding box size relative to frame (quality gate)
    MIN_FACE_RATIO = 0.10   # Face must occupy at least 10% of frame height
    MAX_FACE_RATIO = 0.80   # Face must not be too close

    # Min detection confidence score from InsightFace
    MIN_DET_SCORE = 0.85

    def __init__(self, engine: FaceEngine):
        self._engine  = engine
        self._session: Optional[EnrollmentSession] = None

    def start_session(self, user_sid: str) -> dict:
        """Begin a new enrollment session."""
        self._session = EnrollmentSession(user_sid=user_sid)
        logger.info("Enrollment session started for SID: %s", user_sid)
        return {
            "status": "started",
            "required_angles": REQUIRED_ANGLES,
            "optional_angles": OPTIONAL_ANGLES,
        }

    def capture_angle(self, angle: str) -> dict:
        """
        Capture and validate a face for the specified angle.
        Returns dict with success, embedding (if success), and error message.
        """
        if self._session is None:
            return {"success": False, "error": "No active enrollment session"}

        if angle in self._session.captured_angles:
            return {"success": False, "error": f"Angle '{angle}' already captured"}

        logger.info("Capturing enrollment frame for angle: %s", angle)

        # Read a burst of frames and pick the best one
        best_embedding, best_score, error_msg = self._capture_best_frame(angle)

        if best_embedding is None:
            self._session.errors.append(f"{angle}: {error_msg}")
            return {"success": False, "error": error_msg, "angle": angle}

        self._session.captured_angles[angle] = best_embedding.tolist()
        logger.info(
            "Angle '%s' captured (score: %.3f). Total: %d/%d",
            angle, best_score,
            len(self._session.captured_angles), len(REQUIRED_ANGLES),
        )

        return {
            "success":   True,
            "angle":     angle,
            "score":     round(best_score, 4),
            "complete":  self._session.is_complete,
            "progress":  f"{len(self._session.captured_angles)}/{len(REQUIRED_ANGLES)} required",
        }

    def finalize(self) -> dict:
        """
        Validate the complete session and return all embeddings.
        Called after all required angles are captured.
        """
        if self._session is None:
            return {"success": False, "error": "No session"}

        if not self._session.is_complete:
            missing = [a for a in REQUIRED_ANGLES if a not in self._session.captured_angles]
            return {
                "success": False,
                "error": f"Missing required angles: {missing}",
            }

        # Cross-validation: verify all captured embeddings are similar to each other
        # (ensures they're all the same person)
        embeddings = [
            np.array(v, dtype=np.float32)
            for v in self._session.captured_angles.values()
        ]
        if not self._validate_embedding_consistency(embeddings):
            return {
                "success": False,
                "error": "Captured faces don't appear to be the same person. Please retry.",
            }

        result = {
            "success":    True,
            "user_sid":   self._session.user_sid,
            "embeddings": list(self._session.captured_angles.items()),
            "count":      self._session.embedding_count,
        }
        logger.info(
            "Enrollment finalized: %d embeddings for SID %s",
            self._session.embedding_count, self._session.user_sid,
        )
        self._session = None
        return result

    def cancel(self) -> None:
        logger.info("Enrollment session cancelled")
        self._session = None

    # ─────────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────────

    def _capture_best_frame(
        self, angle: str, attempts: int = 5
    ) -> tuple[Optional[np.ndarray], float, str]:
        """
        Tries up to `attempts` times to capture a good frame.
        Returns (embedding, score, error_message).
        """
        best_embedding = None
        best_score     = 0.0

        for i in range(attempts):
            frame = self._engine._read_frame()
            if frame is None:
                continue

            faces = self._engine._app.get(frame)

            if len(faces) == 0:
                self._engine._zero_frame(frame)
                continue

            if len(faces) > 1:
                self._engine._zero_frame(frame)
                return None, 0.0, "Multiple faces detected. Please ensure you're alone."

            face = faces[0]

            # Quality gate 1: Detection confidence
            det_score = float(face.det_score)
            if det_score < self.MIN_DET_SCORE:
                self._engine._zero_frame(frame)
                continue

            # Quality gate 2: Face size relative to frame
            frame_h = frame.shape[0]
            x1, y1, x2, y2 = face.bbox
            face_h = y2 - y1
            face_ratio = face_h / frame_h

            if face_ratio < self.MIN_FACE_RATIO:
                self._engine._zero_frame(frame)
                return None, 0.0, "Move closer to the camera"

            if face_ratio > self.MAX_FACE_RATIO:
                self._engine._zero_frame(frame)
                return None, 0.0, "Move further from the camera"

            # Quality gate 3: Liveness
            liveness = self._engine._liveness.score(frame, face)
            if liveness < 0.85:
                self._engine._zero_frame(frame)
                return None, 0.0, "Liveness check failed. Ensure good lighting."

            # This frame passes — check if it's better than previous best
            if det_score > best_score:
                best_score     = det_score
                best_embedding = face.normed_embedding.copy()

            self._engine._zero_frame(frame)
            time.sleep(0.1)  # Brief pause between attempts

        if best_embedding is None:
            return None, 0.0, "No valid face detected. Check lighting and position."

        return best_embedding, best_score, ""

    def _validate_embedding_consistency(
        self, embeddings: list[np.ndarray], threshold: float = 0.35
    ) -> bool:
        """
        Ensures all captured embeddings are from the same person.
        Computes pairwise cosine similarity — all pairs must exceed threshold.
        If any pair is too dissimilar, enrollment is rejected.
        """
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                # Both vectors are L2-normalized; dot product = cosine similarity in [-1, 1]
                sim = float(np.dot(embeddings[i], embeddings[j]))
                logger.debug("Embedding pair (%d,%d) similarity: %.3f", i, j, sim)
                if sim < threshold:
                    logger.warning(
                        "Inconsistent embeddings (%.3f < %.3f) — possible different person",
                        sim, threshold,
                    )
                    return False
        return True
