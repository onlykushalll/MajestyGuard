# MajestyGuard.CVEngine/liveness_detector.py
# Multi-layer passive anti-spoofing. No user action required.
#
# LAYERS:
#   1. LBP texture      — real skin has micro-texture, printed photos are flat
#   2. Specular reflect  — screens have uniform glare, real faces don't
#   3. Color space       — YCbCr skin-tone validation + chromatic moments
#   4. Moiré/frequency   — FFT detects screen pixel-grid artifacts
#   5. Temporal blink    — eye-region pixel tracking (works with 5-point landmarks)
#   6. Face boundary     — detects rectangular photo/screen edges around face
#   7. ONNX anti-spoof   — optional Silent-Face-Anti-Spoofing model (highest weight)

import cv2
import numpy as np
import logging
import os
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("MajestyGuard.Liveness")

_ONNX_AVAILABLE = False
_ort = None
try:
    import onnxruntime as ort
    _ort = ort
    _ONNX_AVAILABLE = True
except ImportError:
    pass


class LivenessDetector:
    _WINDOW = 10
    _MIN_FRAMES_FOR_PASS = 5

    def __init__(self, model_dir: str = ""):
        self._score_history: deque[float] = deque(maxlen=self._WINDOW)
        self._frame_index = 0

        # Temporal blink state
        self._eye_brightness_history: deque[float] = deque(maxlen=30)
        self._blink_count = 0
        self._blink_cooldown = 0
        self._last_blink_frame = 0
        self._in_blink = False

        # Face movement tracking
        self._face_center_history: deque[tuple[float, float]] = deque(maxlen=30)

        # Anti-replay: frame fingerprint history
        self._frame_hashes: deque[int] = deque(maxlen=60)
        self._duplicate_frame_count = 0

        # Depth estimation: landmark geometry history
        self._landmark_ratios: deque[list[float]] = deque(maxlen=20)

        # Color histogram consistency
        self._hist_history: deque[np.ndarray] = deque(maxlen=15)

        # Optional ONNX anti-spoof model
        self._antispoof_session: Optional[Any] = None
        # E-3: initialize attrs so _onnx_antispoof_score works even if model load fails midway
        self._antispoof_input_name: str = "input"
        self._antispoof_h: int = 128
        self._antispoof_w: int = 128
        # E-2: track consecutive ONNX failures — init in __init__ not lazily
        self._onnx_consecutive_failures: int = 0
        self._load_antispoof_model(model_dir)

    def reset_session(self) -> None:
        self._score_history.clear()
        self._frame_index = 0
        self._eye_brightness_history.clear()
        self._blink_count = 0
        self._blink_cooldown = 0
        self._last_blink_frame = 0
        self._in_blink = False
        self._face_center_history.clear()
        self._frame_hashes.clear()
        self._duplicate_frame_count = 0
        self._landmark_ratios.clear()
        self._hist_history.clear()
        self._onnx_consecutive_failures = 0  # E-2: proper field reset

    # Candidate filenames — tries each in order so either naming works
    _ANTISPOOF_FILENAMES = [
        "antispoof_minifasv2.onnx",   # downloaded by download_models.py
        "anti_spoof.onnx",            # legacy / alternative name
        "MiniFASNetV2.onnx",
        "minifasv2_128.onnx",
    ]

    def _load_antispoof_model(self, model_dir: str):
        if not _ONNX_AVAILABLE or not model_dir:
            return

        model_path = None
        for fname in self._ANTISPOOF_FILENAMES:
            candidate = os.path.join(model_dir, fname)
            if os.path.exists(candidate):
                model_path = candidate
                break

        if model_path is None:
            logger.info(
                "No anti-spoof ONNX model found in %s — using heuristic layers only. "
                "Run download_models.py to add Layer 7.", model_dir)
            return

        try:
            opts = _ort.SessionOptions()
            opts.inter_op_num_threads = 1   # keep it lean — single thread for 600KB model
            opts.intra_op_num_threads = 2
            opts.graph_optimization_level = _ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._antispoof_session = _ort.InferenceSession(
                model_path,
                sess_options=opts,
                providers=["DmlExecutionProvider", "CPUExecutionProvider"],
            )

            # Detect expected input size from model metadata
            inp = self._antispoof_session.get_inputs()[0]
            self._antispoof_input_name = inp.name
            shape = inp.shape  # [batch, C, H, W]
            self._antispoof_h = int(shape[2]) if len(shape) > 2 and isinstance(shape[2], int) else 128
            self._antispoof_w = int(shape[3]) if len(shape) > 3 and isinstance(shape[3], int) else 128

            logger.info(
                "Anti-spoof ONNX loaded: %s (input %dx%d)",
                os.path.basename(model_path), self._antispoof_h, self._antispoof_w,
            )
        except Exception as e:
            logger.warning("Failed to load anti-spoof model: %s", e)
            self._antispoof_session = None

    def score(self, frame: np.ndarray, face: Any) -> float:
        roi = self._extract_roi(frame, face)
        if roi is None:
            return 0.0  # S-5: fail-closed — ROI failure = cannot verify liveness = deny

        self._frame_index += 1

        # Anti-replay gate: detect video loops / identical frames
        replay_penalty = self._replay_detection(roi)
        if replay_penalty < 0.3:
            logger.warning("Replay attack detected (duplicate frames)")
            return 0.1

        # Layer 1: LBP texture
        lbp_score = self._lbp_texture_score(roi)

        # Layer 2: Specular reflection
        specular_score = self._specular_score(roi)

        # Layer 3: Color space analysis
        color_score = self._color_space_score(roi)

        # Layer 4: Moiré/frequency analysis
        moire_score = self._moire_score(roi)

        # Layer 5: Temporal blink detection (eye region pixel tracking)
        temporal_score = self._temporal_blink_score(frame, face)

        # Layer 6: Face boundary analysis
        boundary_score = self._boundary_score(frame, face)

        # Layer 7: ONNX anti-spoof model (if available)
        onnx_score = self._onnx_antispoof_score(roi)

        # Layer 8: Depth estimation from landmark geometry
        depth_score = self._depth_geometry_score(face)

        # Layer 9: Color histogram temporal consistency
        hist_score = self._histogram_consistency_score(roi)

        if onnx_score is not None:
            combined = (
                onnx_score       * 0.28 +
                lbp_score        * 0.10 +
                specular_score   * 0.08 +
                color_score      * 0.08 +
                moire_score      * 0.08 +
                temporal_score   * 0.10 +
                boundary_score   * 0.08 +
                depth_score      * 0.10 +
                hist_score       * 0.05 +
                replay_penalty   * 0.05
            )
        else:
            combined = (
                lbp_score        * 0.18 +
                specular_score   * 0.10 +
                color_score      * 0.14 +
                moire_score      * 0.10 +
                temporal_score   * 0.14 +
                boundary_score   * 0.08 +
                depth_score      * 0.12 +
                hist_score       * 0.07 +
                replay_penalty   * 0.07
            )

        self._score_history.append(combined)

        # Require minimum frames before allowing high scores
        if self._frame_index < self._MIN_FRAMES_FOR_PASS:
            smoothed = min(float(np.mean(self._score_history)), 0.75)  # Early frames: capped mean
        else:
            # B-021 FIX: use min() not mean()
            # With mean: 9 genuine (0.9) + 1 spoof (0.1) = 0.82 → might pass (WRONG)
            # With min:  same scenario → 0.1 → always fails (CORRECT)
            smoothed = float(np.min(self._score_history))

        logger.debug(
            "Liveness: LBP=%.2f Spec=%.2f Color=%.2f Moire=%.2f Temp=%.2f "
            "Bound=%.2f Depth=%.2f Hist=%.2f Replay=%.2f ONNX=%s -> %.3f",
            lbp_score, specular_score, color_score, moire_score,
            temporal_score, boundary_score, depth_score, hist_score,
            replay_penalty,
            f"{onnx_score:.2f}" if onnx_score is not None else "N/A",
            smoothed)

        return smoothed

    # ── Layer 1: LBP Texture ─────────────────────────────────────────

    def _lbp_texture_score(self, roi: np.ndarray) -> float:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        lbp = self._compute_lbp(gray)

        hist, _ = np.histogram(lbp.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(float)
        hist /= (hist.sum() + 1e-7)

        entropy = -np.sum(hist * np.log2(hist + 1e-7))

        # Variance of LBP — real skin has higher variance
        lbp_var = float(np.var(lbp.astype(np.float32)))

        # Entropy: photos ~3.5, real faces ~6.5
        entropy_score = float(np.clip((entropy - 3.5) / 3.0, 0.0, 1.0))

        # Variance: photos < 1000, real faces > 2000
        var_score = float(np.clip((lbp_var - 1000) / 2000, 0.0, 1.0))

        return entropy_score * 0.7 + var_score * 0.3

    def _compute_lbp(self, gray: np.ndarray) -> np.ndarray:
        rows, cols = gray.shape
        lbp = np.zeros_like(gray)
        offsets = [(-1,-1), (-1,0), (-1,1), (0,1), (1,1), (1,0), (1,-1), (0,-1)]
        center = gray[1:-1, 1:-1].astype(np.int16)

        for bit, (dr, dc) in enumerate(offsets):
            neighbor = gray[1+dr:rows-1+dr, 1+dc:cols-1+dc].astype(np.int16)
            lbp[1:-1, 1:-1] |= ((neighbor >= center).astype(np.uint8) << bit)

        return lbp

    # ── Layer 2: Specular Reflection ─────────────────────────────────

    def _specular_score(self, roi: np.ndarray) -> float:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]
        glare_mask = (value > 225) & (saturation < 40)

        glare_fraction = float(np.count_nonzero(glare_mask)) / float(glare_mask.size)
        if glare_fraction < 0.05:
            return 0.9

        ys, xs = np.nonzero(glare_mask)
        if xs.size == 0:
            return 0.9

        spatial_std = float((np.std(xs) + np.std(ys)) * 0.5)

        # Screen: high glare fraction + low spatial spread
        if glare_fraction > 0.15 and spatial_std < 30.0:
            return 0.2

        t = np.clip((glare_fraction - 0.05) / 0.10, 0.0, 1.0)
        score = 0.9 - (0.7 * t)
        if spatial_std >= 30.0:
            score = max(score, 0.65)
        return float(np.clip(score, 0.2, 0.9))

    # ── Layer 3: Color Space Analysis ────────────────────────────────

    def _color_space_score(self, roi: np.ndarray) -> float:
        ycrcb = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb[:, :, 1].astype(np.float32)
        cb = ycrcb[:, :, 2].astype(np.float32)

        # Skin tone range in YCrCb (empirically validated across skin tones)
        skin_mask = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
        skin_ratio = float(np.count_nonzero(skin_mask)) / float(skin_mask.size)

        # Real faces: 30-70% of face ROI pixels fall in skin-tone range
        # Photos may have color shift from printing/screen rendering
        if skin_ratio < 0.15:
            skin_score = 0.2
        elif skin_ratio > 0.8:
            skin_score = 0.6  # Too uniform — possible single-color printout
        else:
            skin_score = float(np.clip((skin_ratio - 0.15) / 0.45, 0.3, 1.0))

        # Chromatic moment analysis
        # Real faces have natural Cr/Cb variance from lighting and skin features
        # Printed/screen faces often have compressed or shifted color distributions
        cr_std = float(np.std(cr))
        cb_std = float(np.std(cb))

        # Real face: cr_std 8-25, cb_std 5-20
        # Printed: often < 5 or > 30 (over-saturated screen)
        cr_var_score = 1.0 - float(np.clip(abs(cr_std - 15) / 15, 0.0, 1.0))
        cb_var_score = 1.0 - float(np.clip(abs(cb_std - 12) / 12, 0.0, 1.0))
        chromatic_score = (cr_var_score + cb_var_score) / 2.0

        # Cross-channel correlation — real skin has consistent Cr-Cb relationship
        cr_flat = cr.flatten()
        cb_flat = cb.flatten()
        if cr_flat.std() > 0 and cb_flat.std() > 0:
            corr = float(np.corrcoef(cr_flat, cb_flat)[0, 1])
            corr_score = float(np.clip(corr * 0.5 + 0.5, 0.2, 1.0))
        else:
            corr_score = 0.3

        return skin_score * 0.4 + chromatic_score * 0.35 + corr_score * 0.25

    # ── Layer 4: Moiré/Frequency Analysis ────────────────────────────

    def _moire_score(self, roi: np.ndarray) -> float:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)

        # FFT to detect periodic screen artifacts
        f_transform = np.fft.fft2(gray)
        f_shift = np.fft.fftshift(f_transform)
        magnitude = np.log1p(np.abs(f_shift))

        h, w = magnitude.shape
        cy, cx = h // 2, w // 2

        # Exclude DC component and very low frequencies
        r_inner = 5
        r_outer = min(cy, cx) - 1
        y, x = np.ogrid[:h, :w]
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        ring_mask = (dist >= r_inner) & (dist <= r_outer)

        ring_values = magnitude[ring_mask]
        if ring_values.size == 0:
            return 0.7

        # Screen Moiré creates periodic spikes in frequency domain
        mean_mag = float(np.mean(ring_values))
        std_mag = float(np.std(ring_values))

        if std_mag < 1e-6:
            return 0.7

        # Peak-to-mean ratio — high ratio indicates periodic artifacts
        max_mag = float(np.max(ring_values))
        peak_ratio = (max_mag - mean_mag) / std_mag

        # Screens typically have peak_ratio > 8, real faces < 5
        if peak_ratio > 10:
            return 0.15
        elif peak_ratio > 7:
            return 0.4
        elif peak_ratio < 4:
            return 0.9
        else:
            return float(np.clip(1.0 - (peak_ratio - 4) / 6, 0.3, 0.9))

    # ── Layer 5: Temporal Blink Detection ────────────────────────────
    # Uses eye-region pixel intensity tracking instead of EAR
    # (InsightFace 5-point landmarks only give eye centers, not eyelid points)

    def _temporal_blink_score(self, frame: np.ndarray, face: Any) -> float:
        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 2:
            return 0.5

        # Track face center for micro-movement detection
        bbox = face.bbox
        fc_x = float(bbox[0] + bbox[2]) / 2.0
        fc_y = float(bbox[1] + bbox[3]) / 2.0
        self._face_center_history.append((fc_x, fc_y))

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Extract eye region patches around eye center landmarks
        left_eye = kps[0]
        right_eye = kps[1]

        # Eye patch size proportional to inter-eye distance
        eye_dist = float(np.sqrt((left_eye[0] - right_eye[0])**2 +
                                  (left_eye[1] - right_eye[1])**2))
        patch_h = max(int(eye_dist * 0.25), 6)
        patch_w = max(int(eye_dist * 0.35), 8)

        left_brightness = self._eye_patch_brightness(gray, left_eye, patch_w, patch_h)
        right_brightness = self._eye_patch_brightness(gray, right_eye, patch_w, patch_h)

        if left_brightness is None or right_brightness is None:
            return 0.5

        # Average eye brightness — drops during blink (eyelid occludes iris)
        avg_brightness = (left_brightness + right_brightness) / 2.0
        self._eye_brightness_history.append(avg_brightness)

        if self._blink_cooldown > 0:
            self._blink_cooldown -= 1

        # Blink detection: brightness drops then recovers
        if len(self._eye_brightness_history) >= 3 and self._blink_cooldown == 0:
            vals = list(self._eye_brightness_history)
            current = vals[-1]
            prev = vals[-2]
            before = vals[-3]

            # Detect blink pattern: high -> low -> high
            if not self._in_blink and prev < before * 0.85 and prev < current * 0.85:
                # Completed blink (dip and recovery detected)
                self._blink_count += 1
                self._last_blink_frame = self._frame_index
                self._blink_cooldown = 5  # Prevent double-counting
                self._in_blink = False
                logger.debug("Blink detected (#%d)", self._blink_count)
            elif current < prev * 0.82:
                self._in_blink = True
            elif current > prev * 1.1:
                self._in_blink = False

        # Score based on blink evidence and micro-movement
        blink_score = 0.5
        if self._blink_count >= 1:
            frames_since_blink = self._frame_index - self._last_blink_frame
            if frames_since_blink <= 60:
                blink_score = 0.95
            elif frames_since_blink <= 120:
                blink_score = 0.8
            else:
                blink_score = 0.6

        # Micro-movement: photos/video have unnaturally stable face position
        movement_score = self._micro_movement_score()

        return blink_score * 0.6 + movement_score * 0.4

    def _eye_patch_brightness(self, gray: np.ndarray, eye_center, pw: int, ph: int) -> Optional[float]:
        h, w = gray.shape
        cx, cy = int(eye_center[0]), int(eye_center[1])
        x1 = max(0, cx - pw)
        x2 = min(w, cx + pw)
        y1 = max(0, cy - ph)
        y2 = min(h, cy + ph)
        if x2 <= x1 or y2 <= y1:
            return None
        patch = gray[y1:y2, x1:x2]
        # Weight upper half more (eyelid covers from top)
        mid = patch.shape[0] // 2
        upper = float(patch[:mid, :].mean()) if mid > 0 else float(patch.mean())
        lower = float(patch[mid:, :].mean())
        return upper * 0.6 + lower * 0.4

    def _micro_movement_score(self) -> float:
        if len(self._face_center_history) < 10:
            return 0.5
        centers = np.array(list(self._face_center_history))
        # Frame-to-frame displacement
        diffs = np.diff(centers, axis=0)
        displacements = np.sqrt(diffs[:, 0]**2 + diffs[:, 1]**2)
        mean_disp = float(np.mean(displacements))
        std_disp = float(np.std(displacements))

        # Real face: natural micro-sway, mean_disp 0.5-5px, non-zero std
        # Photo: near-zero displacement (static mount) OR very uniform (video replay)
        if mean_disp < 0.1:
            return 0.2  # Completely static — likely photo
        if std_disp < 0.05 and mean_disp > 0.3:
            return 0.3  # Uniform motion — possible video replay (panning phone)
        if mean_disp > 15:
            return 0.4  # Too much movement — shaking phone with photo

        return float(np.clip(0.5 + std_disp * 0.3, 0.5, 0.95))

    # ── Layer 6: Face Boundary Analysis ──────────────────────────────

    def _boundary_score(self, frame: np.ndarray, face: Any) -> float:
        bbox = face.bbox
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]

        # Expand bbox outward to check for photo/screen edges
        expand = int((x2 - x1) * 0.4)
        ox1 = max(0, x1 - expand)
        oy1 = max(0, y1 - expand)
        ox2 = min(w, x2 + expand)
        oy2 = min(h, y2 + expand)

        outer_roi = frame[oy1:oy2, ox1:ox2]
        if outer_roi.size == 0:
            return 0.7

        gray = cv2.cvtColor(outer_roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        # Hough line detection — photos held up have strong straight edges
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                                minLineLength=30, maxLineGap=10)

        if lines is None:
            return 0.9  # No strong lines — likely real

        # Count long straight lines (photo/phone edges)
        long_lines = 0
        for line in lines:
            x1l, y1l, x2l, y2l = line[0]
            length = np.sqrt((x2l - x1l)**2 + (y2l - y1l)**2)
            if length > min(outer_roi.shape[:2]) * 0.4:
                long_lines += 1

        if long_lines >= 4:
            return 0.2  # Rectangular frame detected — likely photo/phone
        elif long_lines >= 2:
            return 0.5
        else:
            return 0.85

    # ── Layer 7: ONNX Anti-Spoof Model ───────────────────────────────

    def _onnx_antispoof_score(self, roi: np.ndarray) -> Optional[float]:
        """
        MiniFASNetV2 anti-spoof inference.

        Model: minifasv2_128.onnx (facenox/face-antispoof-onnx)
        Input:  [1, 3, H, W] float32  — RGB, normalized to [0, 1]
        Output: [1, 2] float32        — [spoof_logit, real_logit]

        The model uses Fourier-transform auxiliary loss during training,
        making it robust against frequency-domain spoofing attacks
        (printed photos with visible patterns, screen glare artifacts).
        """
        if self._antispoof_session is None:
            return None

        try:
            h = self._antispoof_h
            w = self._antispoof_w
            input_name = self._antispoof_input_name

            # Resize ROI to model's expected input size
            resized = cv2.resize(roi, (w, h), interpolation=cv2.INTER_LINEAR)

            # Convert BGR→RGB, normalize [0,255]→[0,1], add batch dim
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

            # HWC → CHW → NCHW
            chw  = np.transpose(rgb, (2, 0, 1))
            blob = np.expand_dims(chw, axis=0)  # [1, 3, H, W]

            # Run inference
            outputs = self._antispoof_session.run(None, {input_name: blob})
            logits  = outputs[0][0]  # [2] — [spoof, real]

            # Softmax
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / exp_logits.sum()

            # Index 0 = spoof, index 1 = real (MiniFASNetV2 convention)
            # If model has different convention, the score will be near 0 for real faces
            # and the heuristic layers will compensate. Calibrate with your own data.
            real_prob = float(probs[1]) if len(probs) >= 2 else float(probs[0])

            logger.debug("ONNX antispoof: real=%.3f spoof=%.3f", real_prob, 1.0 - real_prob)
            return float(np.clip(real_prob, 0.0, 1.0))

        except Exception as e:
            # B-029 FIX: do NOT null the session on single-frame failure.
            # Attacker could trigger one ONNX error to permanently disable Layer 7.
            self._onnx_consecutive_failures += 1
            if self._onnx_consecutive_failures >= 5:
                logger.warning("ONNX anti-spoof: %d consecutive failures — model may be broken: %s",
                               self._onnx_consecutive_failures, e)
            else:
                logger.debug("ONNX inference failed this frame (retry next): %s", e)
            return None  # Skip this frame only — session stays alive

    # ── ROI Extraction ───────────────────────────────────────────────

    def _extract_roi(self, frame: np.ndarray, face: Any) -> Optional[np.ndarray]:
        try:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            pad_x = int((x2 - x1) * 0.20)
            pad_y = int((y2 - y1) * 0.20)

            h, w = frame.shape[:2]
            x1 = max(0, x1 - pad_x)
            y1 = max(0, y1 - pad_y)
            x2 = min(w, x2 + pad_x)
            y2 = min(h, y2 + pad_y)

            if x2 <= x1 or y2 <= y1:
                return None

            roi = frame[y1:y2, x1:x2]
            roi = cv2.resize(roi, (128, 128))
            return roi
        except Exception as e:
            logger.error("ROI extraction failed: %s", e)
            return None

    # ── Layer 8: Depth Geometry Estimation ────────────────────────────
    # Uses ratios between facial landmarks to detect flat images.
    # Real 3D faces produce varying ratios as head moves;
    # flat photos/screens keep ratios constant.

    def _depth_geometry_score(self, face: Any) -> float:
        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 5:
            return 0.5

        left_eye = kps[0]
        right_eye = kps[1]
        nose = kps[2]
        left_mouth = kps[3]
        right_mouth = kps[4]

        eye_dist = float(np.sqrt((left_eye[0] - right_eye[0])**2 +
                                  (left_eye[1] - right_eye[1])**2))
        if eye_dist < 1.0:
            return 0.3

        # Compute geometric ratios that vary with head pose
        nose_to_left = float(np.sqrt((nose[0] - left_eye[0])**2 +
                                      (nose[1] - left_eye[1])**2))
        nose_to_right = float(np.sqrt((nose[0] - right_eye[0])**2 +
                                       (nose[1] - right_eye[1])**2))
        mouth_width = float(np.sqrt((left_mouth[0] - right_mouth[0])**2 +
                                     (left_mouth[1] - right_mouth[1])**2))

        # Asymmetry ratio: nose offset from eye midline
        asymmetry = abs(nose_to_left - nose_to_right) / eye_dist
        # Vertical proportion: eye-to-nose vs eye-to-mouth
        eye_mid_y = (left_eye[1] + right_eye[1]) / 2.0
        mouth_mid_y = (left_mouth[1] + right_mouth[1]) / 2.0
        upper_face = abs(nose[1] - eye_mid_y)
        lower_face = abs(mouth_mid_y - nose[1])
        vert_ratio = upper_face / (lower_face + 1e-6)
        # Mouth-to-eye width ratio
        width_ratio = mouth_width / eye_dist

        ratios = [asymmetry, vert_ratio, width_ratio]
        self._landmark_ratios.append(ratios)

        if len(self._landmark_ratios) < 5:
            return 0.5

        # Real faces: ratios vary naturally as head micro-sways
        # Photos/screens: ratios are nearly constant
        ratios_arr = np.array(list(self._landmark_ratios))
        ratio_stds = np.std(ratios_arr, axis=0)
        mean_variance = float(np.mean(ratio_stds))

        if mean_variance < 0.003:
            return 0.2  # Too rigid — likely flat image
        if mean_variance < 0.008:
            return 0.5
        if mean_variance > 0.05:
            return 0.4  # Too erratic — shaking a phone
        return float(np.clip(0.5 + mean_variance * 30, 0.5, 0.95))

    # ── Layer 9: Color Histogram Temporal Consistency ─────────────────
    # Real faces have natural micro-variations in color over time.
    # Screens/photos show unnatural color stability or periodic patterns.

    def _histogram_consistency_score(self, roi: np.ndarray) -> float:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [18, 8],
                            [0, 180, 0, 256]).flatten().astype(np.float32)
        hist /= (hist.sum() + 1e-7)
        self._hist_history.append(hist)

        if len(self._hist_history) < 5:
            return 0.5

        # Compare consecutive histograms
        similarities = []
        hists = list(self._hist_history)
        for i in range(1, len(hists)):
            sim = float(cv2.compareHist(hists[i-1], hists[i], cv2.HISTCMP_CORREL))
            similarities.append(sim)

        mean_sim = float(np.mean(similarities))
        std_sim = float(np.std(similarities))

        # Real face: natural variation, mean_sim 0.85-0.97
        # Screen replay: very high consistency (>0.99) or periodic patterns
        if mean_sim > 0.995:
            return 0.2  # Unnaturally stable — static image or looped video
        if std_sim < 0.001 and mean_sim > 0.98:
            return 0.3  # No variation at all
        if mean_sim < 0.7:
            return 0.4  # Too much variation — lighting flicker from screen
        return float(np.clip(0.5 + (1.0 - mean_sim) * 5, 0.5, 0.95))

    # ── Anti-Replay: Frame Fingerprint Deduplication ──────────────────
    # Detects video loop attacks by comparing frame hashes.

    def _replay_detection(self, roi: np.ndarray) -> float:
        small = cv2.resize(roi, (16, 16))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        frame_hash = hash(gray.tobytes())

        # Count how many recent frames have identical hash
        matches = sum(1 for h in self._frame_hashes if h == frame_hash)
        self._frame_hashes.append(frame_hash)

        if matches == 0:
            self._duplicate_frame_count = max(0, self._duplicate_frame_count - 1)
            return 0.95

        self._duplicate_frame_count += 1

        # Allow up to 2 identical frames (camera can produce duplicates at low FPS)
        if self._duplicate_frame_count <= 2:
            return 0.8
        if self._duplicate_frame_count <= 5:
            return 0.5
        # Many duplicates = video loop or static photo
        return 0.1

# ── FIX-022: rPPG stub — future liveness Layer 10 ────────────────────────
class rPPGDetector:
    """
    Remote Photoplethysmography — detects blood flow from skin colour changes.
    Real faces: periodic colour variation from cardiac cycle (60-100 BPM).
    Photos, screens, masks: no pulsatile signal.

    IMPLEMENTATION STATUS: Stub. Integrated at weight 0.0 (no effect yet).

    Full implementation requires:
      - 45 frames at 15 FPS = 3 second window
      - Green channel (G) extracted from forehead ROI
      - Bandpass filter 0.7–4.0 Hz (heart rate range)
      - FFT peak detection: SNR > 2.0 = real face
    LIBRARY: pip install pyVHR  (MIT, has ONNX export path)
    LATENCY: 3s minimum — use AFTER recognition succeeds, not as a gate.
    REFERENCE: De Haan & Jeanne (2013), CHROM algorithm.
    """

    def score(self, frame_history: list) -> float:
        # TODO: implement CHROM rPPG algorithm
        return 0.5  # Neutral — does not affect liveness decision until implemented
