# MajestyGuard.CVEngine/face_engine.py
# Core computer vision pipeline.
# Handles: face detection, recognition, embedding generation, liveness check.
#
# MODEL: InsightFace with buffalo_l (ArcFace backbone)
#   - Detection:    RetinaFace (bundled in buffalo_l)
#   - Recognition:  ArcFace R100 — 512-dim embeddings
#   - Liveness:     Custom LBP-based texture classifier (liveness_detector.py)
#
# PERFORMANCE BUDGET:
#   - Inference: ≤ 200ms per frame on Intel Iris Xe (no discrete GPU)
#   - Use ONNX Runtime with DirectML execution provider on Windows
#   - CPU fallback if DirectML unavailable
#
# CODEX NOTES:
#   - buffalo_l downloads ~300MB on first run. Pre-download in installer.
#   - Camera frames are NEVER written to disk. Zero after use.
#   - All processing is 100% local. No network calls.

import os
import time
import logging
import subprocess
import re
import numpy as np
from dataclasses import dataclass
from typing import Optional
import cv2

# InsightFace — install: pip install insightface onnxruntime-directml
try:
    import insightface
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    logging.warning("InsightFace not installed. Run: pip install insightface onnxruntime-directml")

from liveness_detector import LivenessDetector
from virtual_camera_detector import is_virtual_camera, invalidate_camera_cache

logger = logging.getLogger("MajestyGuard.CVEngine")


@dataclass
class FrameResult:
    """Result of processing a single camera frame."""
    face_count: int                    # Raw detected face count (no recognition)
    primary_user_present: bool         # Enrolled user recognized
    recognition_score: float           # Cosine similarity (0.0–1.0)
    liveness_score: float              # Anti-spoofing confidence (0.0–1.0)
    liveness_passed: bool              # True = real face, False = spoof
    virtual_camera_detected: bool      # True = software camera feed
    camera_obstructed: bool            # True = camera appears blocked
    inference_ms: float                # Processing time


class FaceEngine:
    """
    Main CV engine. Initialized once, processes frames continuously.
    """

    def __init__(self, model_dir: str, camera_idx: int = 0, recognition_threshold: float = 0.75):
        self.model_dir  = model_dir
        self.camera_idx = camera_idx
        self.recognition_threshold = recognition_threshold
        self._app: Optional[FaceAnalysis] = None
        self._liveness = LivenessDetector(model_dir=model_dir)
        self._enrolled_embeddings: list[np.ndarray] = []
        self._cap: Optional[cv2.VideoCapture] = None

        # Virtual camera detection: track camera device path
        self._expected_device_path: Optional[str] = None
        self._backend_name: str = ""
        self._det_size: tuple[int, int] = (160, 160)
        self._virtual_camera_cache: tuple[float, bool] = (0.0, False)

        # Multi-frame consensus: require consecutive matches before accepting
        self._consecutive_matches = 0
        self._consecutive_liveness = 0
        self._consensus_threshold = 3  # frames
        self._last_recognition_score = 0.0

    # ─────────────────────────────────────────────────────────────────
    # INITIALIZATION
    # ─────────────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """
        Load models and open camera. Call once at startup.
        Returns True if successful.
        """
        if not INSIGHTFACE_AVAILABLE:
            logger.error("InsightFace is required. Install it first.")
            return False

        try:
            logger.info("Loading InsightFace buffalo_l model from: %s", self.model_dir)

            # providers: DirectML for GPU, fall back to CPU
            # CODEX: Test DirectML provider on target hardware.
            # If unstable, default to CPUExecutionProvider.
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]

            self._app = FaceAnalysis(
                name="buffalo_l",
                root=self.model_dir,
                providers=providers,
            )
            # Start in low-RAM idle mode. Service raises this to 320x320 only while verifying.
            self._app.prepare(ctx_id=0, det_size=self._det_size)

            logger.info("InsightFace loaded successfully")

            # Open camera
            if not self._open_camera():
                return False

            # Validate camera is real hardware (not virtual)
            self._expected_device_path = self._get_camera_device_path(self.camera_idx)

            return True

        except Exception as e:
            logger.error("FaceEngine initialization failed: %s", e)
            return False

    def _open_camera(self) -> bool:
        self._cap = cv2.VideoCapture(self.camera_idx, cv2.CAP_DSHOW)  # DSHOW on Windows
        if not self._cap.isOpened():
            logger.error("Failed to open camera %d", self.camera_idx)
            return False

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        try:
            self._backend_name = self._cap.getBackendName()
        except Exception:
            self._backend_name = "unknown"

        logger.info("Camera %d opened (640x480, backend=%s)", self.camera_idx, self._backend_name)
        return True

    # ─────────────────────────────────────────────────────────────────
    # ENROLLMENT
    # Called during setup to generate and return a face embedding.
    # The embedding is sent back to the Service and stored via EmbeddingStore.
    # ─────────────────────────────────────────────────────────────────

    def set_det_size(self, width: int, height: int) -> None:
        """Switch InsightFace detector input size without reloading the model."""
        width = 320 if width >= 320 else 160
        height = 320 if height >= 320 else 160
        requested = (width, height)
        if requested == self._det_size or self._app is None:
            return

        try:
            self._app.prepare(ctx_id=0, det_size=requested)
            self._det_size = requested
            logger.info("Detector size set to %sx%s", width, height)
        except Exception as e:
            logger.error("Failed to switch detector size to %s: %s", requested, e)
    def capture_enrollment_frame(self) -> Optional[np.ndarray]:
        """
        Captures one frame and returns a 512-dim face embedding.
        Returns None if no face detected or liveness fails.
        """
        frame = self._read_frame()
        if frame is None:
            return None

        faces = self._app.get(frame)

        if len(faces) == 0:
            logger.warning("No face detected during enrollment capture")
            return None

        if len(faces) > 1:
            logger.warning("Multiple faces during enrollment — require single face")
            return None

        face = faces[0]

        # Liveness check during enrollment — don't enroll a photo
        liveness_score = self._liveness.score(frame, face)
        if liveness_score < 0.85:
            logger.warning("Liveness check failed during enrollment (%.3f)", liveness_score)
            return None

        embedding = face.normed_embedding  # 512-dim L2-normalized vector
        logger.info("Enrollment embedding captured (liveness: %.3f)", liveness_score)

        # Return a copy — don't hold a reference to the face object
        result = embedding.copy()
        return result

    def load_enrolled_embeddings(self, embeddings: list[list[float]]) -> None:
        """
        Load pre-computed embeddings from EmbeddingStore into memory.
        Called by cv_server.py after deserializing from the Service.
        """
        self._enrolled_embeddings = [np.array(e, dtype=np.float32) for e in embeddings]
        logger.info("Loaded %d enrolled embeddings", len(self._enrolled_embeddings))

    # ─────────────────────────────────────────────────────────────────
    # MAIN PROCESSING LOOP (called by cv_server.py)
    # ─────────────────────────────────────────────────────────────────

    def process_frame(self) -> FrameResult:
        """
        Reads one camera frame and returns a FrameResult.
        This is the hot path — optimize every branch.
        """
        t_start = time.perf_counter()

        # ── Virtual camera detection ──────────────────────────────────
        if self._is_virtual_camera():
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=True,
                camera_obstructed=False,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        # ── Capture frame ─────────────────────────────────────────────
        frame = self._read_frame()

        if frame is None or self._is_obstructed(frame):
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=False,
                camera_obstructed=True,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        # ── Face detection ────────────────────────────────────────────
        faces = self._app.get(frame)
        face_count = len(faces)

        if face_count == 0:
            self._zero_frame(frame)
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=False,
                camera_obstructed=False,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        # ── Liveness check (runs on LARGEST face = assumed primary) ──
        primary_face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        liveness_score  = self._liveness.score(frame, primary_face)
        liveness_passed = liveness_score >= 0.85

        # Track consecutive liveness passes for consensus
        if liveness_passed:
            self._consecutive_liveness += 1
        else:
            self._consecutive_liveness = 0

        # ── Recognition ───────────────────────────────────────────────
        primary_user_present = False
        best_score           = 0.0

        if liveness_passed and len(self._enrolled_embeddings) > 0:
            query_embedding = primary_face.normed_embedding

            for enrolled in self._enrolled_embeddings:
                score = float(np.dot(query_embedding, enrolled))
                best_score = max(best_score, score)

            # Multi-frame consensus: require N consecutive frames with
            # both recognition match AND liveness pass before declaring present
            if best_score >= self.recognition_threshold:
                self._consecutive_matches += 1
            else:
                self._consecutive_matches = 0

            primary_user_present = (
                self._consecutive_matches >= self._consensus_threshold and
                self._consecutive_liveness >= self._consensus_threshold
            )
            self._last_recognition_score = best_score
        else:
            self._consecutive_matches = 0

        # ── Zero face embedding data (privacy) ──────────────────────
        for face in faces:
            if hasattr(face, "normed_embedding") and face.normed_embedding is not None:
                face.normed_embedding[:] = 0
            if hasattr(face, "embedding") and face.embedding is not None:
                face.embedding[:] = 0

        # ── Zero frame memory ─────────────────────────────────────────
        self._zero_frame(frame)

        inference_ms = (time.perf_counter() - t_start) * 1000
        logger.debug(
            "Frame: faces=%d user=%s score=%.3f liveness=%.3f (%dms)",
            face_count, primary_user_present, best_score, liveness_score, inference_ms,
        )

        return FrameResult(
            face_count=face_count,
            primary_user_present=primary_user_present,
            recognition_score=best_score,
            liveness_score=liveness_score,
            liveness_passed=liveness_passed,
            virtual_camera_detected=False,
            camera_obstructed=False,
            inference_ms=inference_ms,
        )

    # ─────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────

    def _read_frame(self) -> Optional[np.ndarray]:
        if self._cap is None or not self._cap.isOpened():
            return None
        try:
            ret, frame = self._cap.read()
            return frame if ret else None
        except cv2.error as e:
            logger.error("Camera read failed: %s", e)
            return None

    def _is_obstructed(self, frame: np.ndarray) -> bool:
        """
        Detects if camera is physically covered.
        Heuristic: if mean brightness < 8 (out of 255), assume blocked.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) < 8.0

    def _is_virtual_camera(self) -> bool:
        """
        Detects virtual/software cameras via three independent methods:
          1. Registry CLSID blocklist (OBS, ManyCam, XSplit, DroidCam etc.)
          2. SetupAPI hardware ID — physical = USB\VID, virtual = ROOT\ or SWD\
          3. Device friendly name keyword match
        All three run in virtual_camera_detector.py with 30s caching.
        This replaces the ffmpeg-based approach which only checked names.
        """
        return is_virtual_camera(self.camera_idx)

    def _get_camera_device_path(self, index: int) -> Optional[str]:
        """Return a stable camera DeviceID when WMI is available."""
        try:
            proc = subprocess.run(
                ["wmic", "path", "Win32_PnPEntity", "where", "PNPClass='Camera'", "get", "Name,DeviceID"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if len(lines) <= 1:
                return self._backend_name or None

            devices = lines[1:]
            if 0 <= index < len(devices):
                selected = devices[index]
            else:
                selected = devices[0]
            logger.info("Camera device path: %s", selected)
            return selected
        except Exception as e:
            logger.debug("Camera DeviceID lookup failed: %s", e)
            return self._backend_name or None
    @staticmethod
    def _zero_frame(frame: np.ndarray) -> None:
        """Zero out frame buffer from memory. Prevents frame retention."""
        frame[:] = 0

    def shutdown(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
        logger.info("FaceEngine shut down")
