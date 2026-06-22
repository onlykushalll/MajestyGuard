# MajestyGuard.CVEngine/liveness_detector.py
# 12-layer passive anti-spoofing. No user action required.
#
# PER-FRAME LAYERS (fast, every frame):
#   1. LBP texture       — real skin has micro-texture, printed photos are flat
#   2. Specular reflect   — screens have uniform glare, real faces don't
#   3. Color space        — YCbCr skin-tone + chromatic moments + Cr-Cb correlation
#   4. Moiré/frequency    — FFT detects screen pixel-grid artifacts
#   5. Temporal blink     — eye-region pixel tracking (works with 5-point landmarks)
#   6. Face boundary      — detects rectangular photo/screen edges around face
#   7. ONNX anti-spoof    — MiniFASNetV2 (supporting per-frame signal)
#   8. Depth geometry     — landmark ratio variance (flat images stay rigid)
#   9. Histogram consist. — colour distribution temporal stability
#  10. MiDaS depth        — monocular 3-D face map (strong spoof signal)
#
# TEMPORAL LAYERS (stateful, require multiple frames):
#  11. rPPG blood flow    — CHROM cardiac signal from skin colour
#  12. Attention/gaze     — MediaPipe iris: must look at camera
#
# Anti-replay gate: frame fingerprint dedup detects video loops.

import cv2
import numpy as np
import hashlib
import logging
import os
from collections import deque
from typing import Any, Optional

from attention_detector import AttentionDetector
from depth_liveness import DepthLivenessDetector
from face_quality import measure_face_quality
from rppg_detector import CHROMrPPGDetector

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
    _WINDOW = 30           # 2 seconds at 15 FPS — 10th percentile tolerates ~3 bad frames
    _MIN_FRAMES_FOR_PASS = 5
    _MIN_USABLE_FACE_QUALITY = 0.40

    def __init__(self, model_dir: str = ""):
        self._score_history: deque[float] = deque(maxlen=self._WINDOW)
        self._frame_index = 0
        self._last_smoothed_score = 0.0

        # Temporal blink state
        self._eye_brightness_history: deque[float] = deque(maxlen=30)
        self._blink_count = 0
        self._blink_cooldown = 0
        self._last_blink_frame = 0
        self._in_blink = False

        # Face movement tracking
        self._face_center_history: deque[tuple[float, float]] = deque(maxlen=30)

        # Anti-replay: frame fingerprint history
        self._frame_hashes: deque[bytes] = deque(maxlen=60)
        self._duplicate_frame_count = 0

        # Depth estimation: landmark geometry history
        self._landmark_ratios: deque[list[float]] = deque(maxlen=20)

        # Color histogram consistency
        self._hist_history: deque[np.ndarray] = deque(maxlen=15)

        # ONNX anti-spoof can produce isolated low spikes on real webcam frames
        # when the face crop jitters. Median-of-5 keeps sustained spoof signals
        # low while preventing one bad frame from poisoning the 10th percentile.
        self._onnx_score_history: deque[float] = deque(maxlen=5)

        # Add-on liveness layers.
        self._depth_liveness = DepthLivenessDetector(model_dir) if model_dir else None
        self._rppg = CHROMrPPGDetector()
        self._attention = AttentionDetector()

        # Optional ONNX anti-spoof model
        self._antispoof_session: Optional[Any] = None
        # E-3: initialize attrs so _onnx_antispoof_score works even if model load fails midway
        self._antispoof_input_name: str = "input"
        self._antispoof_h: int = 128
        self._antispoof_w: int = 128
        self._last_onnx_idx0: float = float("nan")
        self._last_onnx_idx1: float = float("nan")
        # E-2: track consecutive ONNX failures — init in __init__ not lazily
        self._onnx_consecutive_failures: int = 0
        self._load_antispoof_model(model_dir)

    def reset_session(self) -> None:
        self._score_history.clear()
        self._frame_index = 0
        self._last_smoothed_score = 0.0
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
        self._onnx_score_history.clear()
        if self._rppg is not None:
            self._rppg.reset()
        self._onnx_consecutive_failures = 0  # E-2: proper field reset

    def close(self) -> None:
        """Release optional detector resources. Non-blocking — never hangs."""
        if self._attention is not None:
            try:
                self._attention.close()
            except Exception:
                pass

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

    def score_fast(self, frame: np.ndarray, face: Any) -> float:
        """
        Fast-path liveness for burst verification.

        Uses only per-frame layers and deliberately skips rPPG, attention,
        MiDaS, and histogram temporal consistency. The daemon applies a
        stricter threshold to this score before clearing the soft lock.
        """
        quality = measure_face_quality(frame, face)
        if quality.score < self._MIN_USABLE_FACE_QUALITY:
            logger.debug(
                "Skipping fast liveness frame below quality floor: q=%.2f sharp=%.1f illum=%.1f h=%.2f center=%.2f",
                quality.score,
                quality.sharpness,
                quality.illumination_mean,
                quality.height_frac,
                quality.center_offset,
            )
            return 0.0

        roi = self._extract_roi(frame, face)
        if roi is None:
            return 0.0

        self._frame_index += 1
        replay_penalty = self._replay_detection(roi)
        if replay_penalty < 0.3:
            logger.warning("Replay attack detected during fast liveness")
            return 0.1

        lbp_score = self._lbp_texture_score(roi)
        specular_score = self._specular_score(roi)
        temporal_score = self._temporal_blink_score(frame, face)
        boundary_score = self._boundary_score(frame, face)
        onnx_score = self._onnx_antispoof_score(roi)
        depth_score = self._depth_geometry_score(face)

        if onnx_score is not None:
            combined = (
                onnx_score * 0.28
                + lbp_score * 0.18
                + specular_score * 0.12
                + temporal_score * 0.12
                + boundary_score * 0.12
                + depth_score * 0.10
                + replay_penalty * 0.08
            )
        else:
            combined = (
                lbp_score * 0.24
                + specular_score * 0.16
                + temporal_score * 0.16
                + boundary_score * 0.16
                + depth_score * 0.14
                + replay_penalty * 0.14
            )

        logger.debug(
            "Fast liveness: LBP=%.2f Spec=%.2f Temp=%.2f Bound=%.2f Depth=%.2f Replay=%.2f ONNX=%s -> %.3f",
            lbp_score,
            specular_score,
            temporal_score,
            boundary_score,
            depth_score,
            replay_penalty,
            f"{onnx_score:.2f}" if onnx_score is not None else "N/A",
            combined,
        )
        return float(np.clip(combined, 0.0, 1.0))

    def score_full(self, frame: np.ndarray, face: Any) -> float:
        quality = measure_face_quality(frame, face)
        if quality.score < self._MIN_USABLE_FACE_QUALITY:
            logger.debug(
                "Skipping liveness frame below quality floor: q=%.2f sharp=%.1f illum=%.1f h=%.2f center=%.2f",
                quality.score,
                quality.sharpness,
                quality.illumination_mean,
                quality.height_frac,
                quality.center_offset,
            )
            return self._last_smoothed_score if self._score_history else 0.0

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

        # Layer 10: MiDaS monocular depth (neutral if model is missing)
        midas_score = (
            self._depth_liveness.score(frame, face)
            if self._depth_liveness is not None else 0.5
        )

        # Layer 11/12: rPPG blood-flow and MediaPipe iris attention.
        rppg_score = self._rppg.update(frame, face)
        attention_score = self._attention.score(frame)

        if onnx_score is not None:
            combined = (
                onnx_score       * 0.10 +
                lbp_score        * 0.13 +
                specular_score   * 0.08 +
                color_score      * 0.09 +
                moire_score      * 0.10 +
                temporal_score   * 0.10 +
                boundary_score   * 0.09 +
                depth_score      * 0.09 +
                hist_score       * 0.08 +
                replay_penalty   * 0.14
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

        if self._depth_liveness is not None and self._depth_liveness.available:
            # MiDaS on a monocular RGB-only webcam returns 0.40-0.72 even for real faces
            # (it has no IR / structured light to get true depth). Applying it
            # unconditionally drags combined from ~0.83 down to ~0.77 for no gain.
            # Only blend when MiDaS gives a DEFINITIVE verdict:
            #   < 0.38 → strong spoof signal (flat screen / printed photo) → penalise
            #   > 0.72 → strong real signal → boost
            #   0.38-0.72 → uncertain (monocular RGB typical range) → skip
            if midas_score < 0.38:
                combined = combined * 0.85 + midas_score * 0.15   # penalise spoof
            elif midas_score > 0.72:
                combined = combined * 0.88 + midas_score * 0.12   # boost real

        if self._rppg.has_signal:
            # rPPG + attention are supplementary. They can lightly penalize a
            # face with no physiological/attention signal, but a noisy rPPG
            # estimate must not drag down otherwise strong real-face evidence.
            if rppg_score >= 0.60 or attention_score >= 0.75:
                combined = min(
                    0.98,
                    combined
                    + max(0.0, rppg_score - 0.50) * 0.06
                    + max(0.0, attention_score - 0.50) * 0.04,
                )
            else:
                combined = combined * 0.94 + rppg_score * 0.04 + attention_score * 0.02

        self._score_history.append(combined)

        # Require minimum frames before allowing high scores
        if self._frame_index < self._MIN_FRAMES_FOR_PASS:
            smoothed = min(float(np.mean(self._score_history)), 0.75)
        else:
            # Use a sliding window of the last 30 frames (2 seconds at 15 FPS).
            # Aggregate with 10th percentile instead of global min():
            #   - Global min() kept every early warm-up frame forever — one 0.48
            #     frame from frame 1 would block unlock indefinitely. BAD.
            #   - Pure mean() is too lenient — 9×0.9 + 1×0.1 spoof = 0.82. WRONG.
            #   - 10th percentile of a 30-frame window: robust against occasional
            #     bad frames (blink, motion) but a sustained spoof will have
            #     multiple bad frames and will fail. CORRECT.
            window = list(self._score_history)[-30:]
            smoothed = float(np.percentile(window, 10))

        self._last_smoothed_score = smoothed

        logger.debug(
            "Liveness: LBP=%.2f Spec=%.2f Color=%.2f Moire=%.2f Temp=%.2f "
            "Bound=%.2f Depth=%.2f Hist=%.2f Replay=%.2f MiDaS=%.2f "
            "rPPG=%.2f Attention=%.2f ONNX=%s -> %.3f",
            lbp_score, specular_score, color_score, moire_score,
            temporal_score, boundary_score, depth_score, hist_score,
            replay_penalty, midas_score, rppg_score, attention_score,
            f"{onnx_score:.2f}" if onnx_score is not None else "N/A",
            smoothed)

        return smoothed

    def score(self, frame: np.ndarray, face: Any) -> float:
        """Backward-compatible alias for the full 12-layer liveness path."""
        return self.score_full(frame, face)

    # ── Layer 1: LBP Texture ─────────────────────────────────────────

    def _lbp_texture_score(self, roi: np.ndarray) -> float:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        lbp = self._compute_lbp(gray)
        # Exclude uncomputed boundary/border pixels
        lbp_inner = lbp[1:-1, 1:-1]

        hist, _ = np.histogram(lbp_inner.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(float)
        hist /= (hist.sum() + 1e-7)

        entropy = -np.sum(hist * np.log2(hist + 1e-7))

        # Variance of LBP — real skin has higher variance
        lbp_var = float(np.var(lbp_inner.astype(np.float32)))

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
        # Work from the central face crop. The ROI has padding for texture and
        # boundary layers; including hair/background makes skin ratio too harsh.
        h, w = roi.shape[:2]
        y1, y2 = int(h * 0.12), int(h * 0.88)
        x1, x2 = int(w * 0.12), int(w * 0.88)
        core = roi[y1:y2, x1:x2]
        if core.size == 0:
            core = roi

        ycrcb = cv2.cvtColor(core, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb[:, :, 1].astype(np.float32)
        cb = ycrcb[:, :, 2].astype(np.float32)
        hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
        hue = hsv[:, :, 0]
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]

        # Skin tone range in YCrCb.
        # Widened for real webcams and diverse skin tones; spoof detection is
        # still carried by texture, moire, ONNX, replay, and temporal layers.
        skin_ycrcb = (cr >= 118) & (cr <= 190) & (cb >= 55) & (cb <= 155)
        skin_hsv = (((hue <= 28) | (hue >= 160)) & (sat >= 18) & (sat <= 210) & (val >= 35))
        skin_mask = skin_ycrcb | skin_hsv
        skin_ratio = float(np.count_nonzero(skin_mask)) / float(skin_mask.size)

        if skin_ratio < 0.10:
            skin_score = 0.35
        elif skin_ratio > 0.85:
            skin_score = 0.70
        else:
            skin_score = float(np.clip(0.45 + (skin_ratio - 0.10) / 0.55, 0.45, 1.0))

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

        score = skin_score * 0.45 + chromatic_score * 0.30 + corr_score * 0.25
        return float(np.clip(score, 0.55, 0.95))

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

        # Count only rectangle-like border lines. The old version counted any
        # long scene edge, so shelves/doors/background geometry could look like
        # a held photo. A spoof frame should show border evidence on multiple
        # sides of the expanded face crop.
        roi_h, roi_w = outer_roi.shape[:2]
        min_dim = min(roi_h, roi_w)
        border_margin = max(12, int(min_dim * 0.12))
        long_lines = 0
        sides = {"top": False, "bottom": False, "left": False, "right": False}

        for line in lines:
            x1l, y1l, x2l, y2l = line[0]
            dx = float(x2l - x1l)
            dy = float(y2l - y1l)
            length = float(np.sqrt(dx * dx + dy * dy))
            if length <= min_dim * 0.38:
                continue

            long_lines += 1
            angle = abs(float(np.degrees(np.arctan2(dy, dx))))
            angle = min(angle, 180.0 - angle)

            if angle <= 12.0:
                avg_y = (float(y1l) + float(y2l)) * 0.5
                if avg_y <= border_margin:
                    sides["top"] = True
                elif avg_y >= roi_h - border_margin:
                    sides["bottom"] = True
            elif abs(angle - 90.0) <= 12.0:
                avg_x = (float(x1l) + float(x2l)) * 0.5
                if avg_x <= border_margin:
                    sides["left"] = True
                elif avg_x >= roi_w - border_margin:
                    sides["right"] = True

        side_count = sum(1 for present in sides.values() if present)
        if side_count >= 4:
            return 0.2  # Rectangular frame detected — likely photo/phone
        if side_count == 3:
            return 0.45
        if side_count == 2 and long_lines >= 4:
            return 0.65
        if long_lines >= 6:
            return 0.72  # busy background: weak signal, not a spoof verdict
        return 0.85

    # ── Layer 7: ONNX Anti-Spoof Model ───────────────────────────────

    def _onnx_antispoof_score(self, roi: np.ndarray) -> Optional[float]:
        """
        MiniFASNetV2 anti-spoof inference.

        Model: minifasv2_128.onnx (facenox/face-antispoof-onnx)
        Input:  [1, 3, H, W] float32  — RGB, normalized to [0, 1]
        Output: [1, 2] float32        — [real_logit, spoof_logit]

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
            logits  = outputs[0][0]  # [2] — bundled model: [real, spoof]

            # Softmax
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / exp_logits.sum()
            self._last_onnx_idx0 = float(probs[0])
            self._last_onnx_idx1 = float(probs[1]) if len(probs) > 1 else float("nan")

            # Some MiniFASNetV2 exports use index 1 = real, index 0 = spoof.
            # HOWEVER — our bundled antispoof_minifasv2.onnx uses the opposite convention
            # (confirmed by diagnostic: real face scores ~0.01 at index 1, ~0.99 at index 0).
            # So index 0 = real for THIS model. Do not flip this without rerunning
            # the per-layer diagnostic against the exact bundled ONNX file.
            if len(probs) >= 2:
                real_prob = float(probs[0])  # index 0 = real in antispoof_minifasv2.onnx
            else:
                real_prob = float(probs[0])

            self._onnx_score_history.append(real_prob)
            if len(self._onnx_score_history) >= 3:
                real_prob = float(np.median(self._onnx_score_history))

            logger.debug("ONNX antispoof: real=%.3f spoof=%.3f (idx0=%.3f idx1=%.3f)",
                         real_prob, 1.0 - real_prob, self._last_onnx_idx0,
                         self._last_onnx_idx1 if len(probs) > 1 else 0.0)
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
            h, w = frame.shape[:2]
            box_w = max(1, x2 - x1)
            box_h = max(1, y2 - y1)

            # Match the upstream MiniFASNet preprocessing: expanded square face
            # crop with reflected borders. Resizing a rectangular crop directly
            # to 128x128 distorts the face and can flip real faces to spoof.
            crop_size = int(max(box_w, box_h) * 1.5)
            if crop_size <= 1:
                return None

            center_x = (x1 + x2) * 0.5
            center_y = (y1 + y2) * 0.5
            crop_x1 = int(center_x - crop_size * 0.5)
            crop_y1 = int(center_y - crop_size * 0.5)
            crop_x2 = crop_x1 + crop_size
            crop_y2 = crop_y1 + crop_size

            src_x1 = max(0, crop_x1)
            src_y1 = max(0, crop_y1)
            src_x2 = min(w, crop_x2)
            src_y2 = min(h, crop_y2)
            if src_x2 <= src_x1 or src_y2 <= src_y1:
                return None

            roi = frame[src_y1:src_y2, src_x1:src_x2]
            top = max(0, -crop_y1)
            left = max(0, -crop_x1)
            bottom = max(0, crop_y2 - h)
            right = max(0, crop_x2 - w)
            if top or bottom or left or right:
                roi = cv2.copyMakeBorder(
                    roi, top, bottom, left, right, cv2.BORDER_REFLECT_101
                )

            interpolation = cv2.INTER_LANCZOS4 if crop_size < 128 else cv2.INTER_AREA
            roi = cv2.resize(roi, (128, 128), interpolation=interpolation)
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
        if mean_variance > 0.12:
            return 0.45  # Very erratic — shaking a phone or unstable detector
        if mean_variance > 0.05:
            return 0.62  # Webcam landmark jitter: weak, not decisive, evidence
        return float(np.clip(0.55 + mean_variance * 10, 0.55, 0.95))

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
            return 0.60

        # Compare consecutive histograms
        similarities = []
        hists = list(self._hist_history)
        for i in range(1, len(hists)):
            sim = float(cv2.compareHist(hists[i-1], hists[i], cv2.HISTCMP_CORREL))
            similarities.append(sim)

        mean_sim = float(np.mean(similarities))
        std_sim = float(np.std(similarities))

        # Real face in normal lighting: mean_sim typically 0.90-0.98.
        # Spoof signals require BOTH near-perfect stability AND near-zero jitter.
        # A real face in a stable environment can have high mean_sim but will
        # still show std_sim > 0.001 from natural micro-movement and lighting.
        if mean_sim > 0.999 and std_sim < 0.0005:
            return 0.45  # Near-perfect stability + zero variance = suspicious, not decisive
        if mean_sim > 0.996 and std_sim < 0.001:
            return 0.55  # Very suspicious but not conclusive alone
        if mean_sim < 0.70:
            return 0.55  # Too much variation — screen flicker or rapid movement
        jitter_score = float(np.clip(std_sim / 0.006, 0.0, 1.0))
        variation_score = float(np.clip((1.0 - mean_sim) / 0.08, 0.0, 1.0))
        return float(np.clip(0.62 + jitter_score * 0.20 + variation_score * 0.13, 0.55, 0.95))

    # ── Anti-Replay: Frame Fingerprint Deduplication ──────────────────
    # Detects video loop attacks by comparing frame hashes.

    def _replay_detection(self, roi: np.ndarray) -> float:
        small = cv2.resize(roi, (16, 16))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        frame_hash = hashlib.md5(gray.tobytes(), usedforsecurity=False).digest()

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
