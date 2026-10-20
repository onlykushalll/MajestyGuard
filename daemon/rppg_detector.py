"""
CHROM rPPG blood-flow liveness detector.

Pure signal processing based on de Haan & Jeanne (2013). Real faces can show a
cardiac signal in the 0.67-4 Hz band; photos and masks should not.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

import cv2
import numpy as np

try:
    from scipy import signal as scipy_signal
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False

logger = logging.getLogger("MajestyGuard.rPPG")


class CHROMrPPGDetector:
    MIN_FRAMES = 45
    WINDOW_FRAMES = 90
    # Legacy fallback sample rate (MG-D-08): used only while fewer than
    # two frame timestamps exist (first call). The frequency math now
    # derives the effective FPS from per-frame time.monotonic() samples
    # over the analysis window (see _effective_fps), clamped to
    # [FPS_MIN, FPS_MAX], so a camera negotiated at e.g. 30 FPS no longer
    # gets its heart-rate band analyzed with 15 FPS scaling.
    FPS = 15.0
    FPS_MIN = 5.0
    FPS_MAX = 60.0
    FREQ_LO = 0.67
    FREQ_HI = 4.00

    def __init__(self):
        self._rgb_buf: deque[tuple[float, float, float]] = deque(maxlen=self.WINDOW_FRAMES)
        # MG-D-08: per-frame monotonic timestamps, appended 1:1 with
        # _rgb_buf so the effective sample rate can be measured.
        self._ts_buf: deque[float] = deque(maxlen=self.WINDOW_FRAMES)
        self._score: float = 0.5
        self._snr: float = 0.0

    def reset(self) -> None:
        self._rgb_buf.clear()
        self._ts_buf.clear()
        self._score = 0.5
        self._snr = 0.0

    @property
    def score(self) -> float:
        return self._score

    @property
    def snr(self) -> float:
        """Raw signal-to-noise ratio from the last successful CHROM
        computation (0.0 if none has run yet this session). score is
        this clipped/normalized into 0-1, which loses the distinction
        between 'no periodic signal at all' (SNR near 1.0, characteristic
        of a flat screen with zero real pulse) and 'a genuinely noisy but
        plausibly real reading' (SNR a bit below the clip floor). Exposed
        separately so a caller can weight a confidently-low reading
        differently from a merely-inconclusive one, without changing the
        existing score/has_signal contract."""
        return self._snr

    @property
    def has_signal(self) -> bool:
        return len(self._rgb_buf) >= self.MIN_FRAMES

    def update(self, frame: np.ndarray, face: Any) -> float:
        if not _SCIPY_OK:
            return 0.5

        roi = self._skin_roi(frame, face)
        if roi is None or roi.size == 0:
            return self._score

        b, g, r = cv2.split(roi)
        red = float(np.mean(r))
        green = float(np.mean(g))
        blue = float(np.mean(b))
        if red < 15 or green < 15:
            return self._score

        self._ts_buf.append(time.monotonic())
        self._rgb_buf.append((red, green, blue))
        if not self.has_signal:
            return 0.5

        self._score = self._chrom()
        return self._score

    def _effective_fps(self) -> float:
        """Measured sample rate over the current analysis window (MG-D-08).

        Derived from the per-frame monotonic timestamps; falls back to the
        legacy 15.0 constant when fewer than two timestamps exist (first
        call / legacy callers that fill _rgb_buf directly), and clamped to
        [FPS_MIN, FPS_MAX] so a broken span cannot push the bandpass or
        FFT bins out of a sane range."""
        if len(self._ts_buf) < 2:
            return self.FPS
        span = self._ts_buf[-1] - self._ts_buf[0]
        if span <= 0.0:
            return self.FPS
        fps = (len(self._ts_buf) - 1) / span
        return float(min(max(fps, self.FPS_MIN), self.FPS_MAX))

    def _chrom(self) -> float:
        frames = np.array(self._rgb_buf, dtype=np.float64)
        red = frames[:, 0]
        green = frames[:, 1]
        blue = frames[:, 2]

        # MG-D-08: use the measured effective sample rate, not the hardcoded
        # 15 FPS constant, for the bandpass and FFT frequency math.
        fps = self._effective_fps()

        red_norm = red / (np.mean(red) + 1e-9)
        green_norm = green / (np.mean(green) + 1e-9)
        blue_norm = blue / (np.mean(blue) + 1e-9)
        x_signal = 3 * red_norm - 2 * green_norm
        y_signal = 1.5 * red_norm + green_norm - 1.5 * blue_norm
        chrom_signal = x_signal - (np.std(x_signal) + 1e-9) / (np.std(y_signal) + 1e-9) * y_signal

        nyquist = fps / 2.0
        try:
            b, a = scipy_signal.butter(
                4,
                [self.FREQ_LO / nyquist, min(self.FREQ_HI / nyquist, 0.98)],
                btype="band",
            )
            chrom_signal = scipy_signal.filtfilt(b, a, chrom_signal)
        except (ValueError, TypeError, FloatingPointError) as e:
            logger.debug("rPPG filter failed: %s", e)
            return 0.5

        sample_count = len(chrom_signal)
        fft = np.abs(np.fft.rfft(chrom_signal, n=sample_count * 2))
        freqs = np.fft.rfftfreq(sample_count * 2, d=1.0 / fps)
        mask = (freqs >= self.FREQ_LO) & (freqs <= self.FREQ_HI)
        if mask.sum() < 2:
            return 0.5

        snr = float(np.max(fft[mask])) / (float(np.mean(fft[mask])) + 1e-9)
        self._snr = snr
        score = float(np.clip((snr - 1.3) / (3.5 - 1.3), 0.0, 1.0))
        logger.debug("rPPG SNR=%.2f -> %.3f", snr, score)
        return score

    def _skin_roi(self, frame: np.ndarray, face: Any) -> np.ndarray | None:
        try:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
        except (AttributeError, TypeError, ValueError, IndexError):
            return None

        face_h = y2 - y1
        face_w = x2 - x1
        roi = frame[
            max(0, y1 + int(face_h * 0.05)):y1 + int(face_h * 0.35),
            max(0, x1 + int(face_w * 0.20)):x2 - int(face_w * 0.20),
        ]
        return roi if roi.size > 0 else None
