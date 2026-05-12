# MajestyGuard.CVEngine/enrollment.py
import os, time, json, logging
import numpy as np
import cv2
from dataclasses import dataclass, field
from typing import Optional
from face_engine import FaceEngine

logger = logging.getLogger("MajestyGuard.Enrollment")

REQUIRED_ANGLES = ["Front", "SlightLeft", "SlightRight"]
OPTIONAL_ANGLES = ["LookUp", "LookDown", "WithGlasses"]


@dataclass
class EnrollmentSession:
    user_sid: str
    # FIX-020: store (embedding, quality) tuples per angle for weighted fusion
    captured_angles: dict[str, tuple[list[float], float]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return all(a in self.captured_angles for a in REQUIRED_ANGLES)

    @property
    def embedding_count(self) -> int:
        return len(self.captured_angles)


class EnrollmentManager:
    MIN_FACE_RATIO = 0.10
    MAX_FACE_RATIO = 0.80
    MIN_DET_SCORE  = 0.85

    def __init__(self, engine: FaceEngine):
        self._engine  = engine
        self._session: Optional[EnrollmentSession] = None

    def start_session(self, user_sid: str) -> dict:
        self._session = EnrollmentSession(user_sid=user_sid)
        logger.info("Enrollment session started for SID: %s", user_sid)
        return {"status": "started", "required_angles": REQUIRED_ANGLES, "optional_angles": OPTIONAL_ANGLES}

    def capture_angle(self, angle: str) -> dict:
        if self._session is None:
            return {"success": False, "error": "No active enrollment session"}
        if angle in self._session.captured_angles:
            return {"success": False, "error": f"Angle '{angle}' already captured"}

        logger.info("Capturing enrollment frame for angle: %s", angle)
        result = self._capture_best_frame(angle)

        if result is None:
            err = "No valid face detected. Check lighting and position."
            self._session.errors.append(f"{angle}: {err}")
            return {"success": False, "error": err, "angle": angle}

        embedding, quality = result
        self._session.captured_angles[angle] = (embedding.tolist(), quality)
        logger.info("Angle '%s' captured (quality: %.3f). Total: %d/%d",
                    angle, quality, len(self._session.captured_angles), len(REQUIRED_ANGLES))
        return {
            "success":  True,
            "angle":    angle,
            "quality":  round(quality, 4),
            "complete": self._session.is_complete,
            "progress": f"{len(self._session.captured_angles)}/{len(REQUIRED_ANGLES)} required",
        }

    def finalize(self) -> dict:
        if self._session is None:
            return {"success": False, "error": "No session"}
        if not self._session.is_complete:
            missing = [a for a in REQUIRED_ANGLES if a not in self._session.captured_angles]
            return {"success": False, "error": f"Missing required angles: {missing}"}

        embeddings = [np.array(e, dtype=np.float32)
                      for e, q in self._session.captured_angles.values()]
        qualities  = [q for e, q in self._session.captured_angles.values()]

        if not self._validate_consistency(embeddings):
            return {"success": False, "error": "Captured faces don't appear to be the same person. Please retry."}

        # FIX-020: quality-weighted fusion into single super-embedding
        fused = self._quality_weighted_fusion(embeddings, qualities)

        result = {
            "success":          True,
            "user_sid":         self._session.user_sid,
            "fused_embedding":  fused.tolist(),       # primary embedding for recognition
            "embeddings":       [(a, e) for a, (e, q)  # individual angles kept as backup
                                 in self._session.captured_angles.items()],
            "count":            self._session.embedding_count,
        }
        logger.info("Enrollment finalized: %d angles, fused embedding ready.", self._session.embedding_count)
        self._session = None
        return result

    def cancel(self) -> None:
        logger.info("Enrollment session cancelled")
        self._session = None

    # ── INTERNAL ──────────────────────────────────────────────────────

    def _capture_best_frame(self, angle: str, attempts: int = 5) -> Optional[tuple[np.ndarray, float]]:
        """Capture best frame over multiple attempts. Returns (embedding, quality) or None."""
        self._engine.reset_liveness()  # AR6: reset liveness state between angle captures
        best_embedding: Optional[np.ndarray] = None
        best_quality   = 0.0

        for _ in range(attempts):
            frame = self._engine.read_frame()
            if frame is None:
                continue

            faces = self._engine.detect_faces(frame)

            if len(faces) == 0 or len(faces) > 1:
                self._engine.zero_frame(frame)
                continue

            face = faces[0]
            det_score = float(face.det_score)
            if det_score < self.MIN_DET_SCORE:
                self._engine.zero_frame(frame)
                continue

            frame_h = frame.shape[0]
            x1, y1, x2, y2 = face.bbox
            face_ratio = (y2 - y1) / frame_h
            if not (self.MIN_FACE_RATIO <= face_ratio <= self.MAX_FACE_RATIO):
                self._engine.zero_frame(frame)
                continue

            liveness = self._engine.check_liveness(frame, face)
            if liveness < 0.85:
                self._engine.zero_frame(frame)
                continue

            # FIX-020: compute frame quality score
            quality = self.compute_frame_quality(frame, face)

            if quality > best_quality:
                best_quality   = quality
                best_embedding = self._engine.get_embedding(frame, face).copy()

            self._engine.zero_frame(frame)
            time.sleep(0.1)

        if best_embedding is None:
            return None
        return best_embedding, best_quality

    def compute_frame_quality(self, frame: np.ndarray, face) -> float:
        """
        FIX-020: Quality score 0.0-1.0 for a face frame.
        Combines detection confidence, sharpness, and illumination balance.
        """
        det_score = float(face.det_score)

        # Sharpness: Laplacian variance on face ROI
        x1, y1, x2, y2 = [int(v) for v in face.bbox]
        roi = frame[max(0, y1):y2, max(0, x1):x2]
        if roi.size == 0:
            return det_score * 0.5

        gray      = cv2.cvtColor(cv2.resize(roi, (64, 64)), cv2.COLOR_BGR2GRAY)
        sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharpness_score = min(sharpness / 500.0, 1.0)

        # Illumination balance: distance from neutral grey (128)
        ycrcb  = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
        mean_y = float(np.mean(ycrcb[:, :, 0]))
        illum_score = 1.0 - abs(mean_y - 128) / 128.0

        return det_score * 0.5 + sharpness_score * 0.3 + illum_score * 0.2

    @staticmethod
    def _quality_weighted_fusion(embeddings: list[np.ndarray], qualities: list[float]) -> np.ndarray:
        """
        FIX-020: Weighted average of embeddings using per-angle quality scores.
        Higher quality angle gets more weight in the final embedding.
        Result is L2-normalised.
        """
        emb_array = np.array(embeddings, dtype=np.float32)    # (N, 512)
        q_array   = np.array(qualities,  dtype=np.float32)    # (N,)
        weights   = q_array / (q_array.sum() + 1e-8)          # normalise weights

        fused = np.sum(emb_array * weights[:, np.newaxis], axis=0)
        norm  = np.linalg.norm(fused)
        return fused / (norm + 1e-8)

    def _validate_consistency(self, embeddings: list[np.ndarray], threshold: float = 0.55) -> bool:
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = float(np.dot(embeddings[i], embeddings[j]))
                if sim < threshold:
                    logger.warning("Inconsistent embeddings (%.3f < %.3f)", sim, threshold)
                    return False
        return True
