# rppg_detector.py — CHROM rPPG blood-flow liveness detector
# de Haan & Jeanne (2013). No model download needed — pure signal processing.
# Real faces: cardiac signal in 0.67-4 Hz band. Photos/masks: no signal.

import numpy as np
import cv2
import logging
from collections import deque
from typing import Any

try:
    from scipy import signal as scipy_signal
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False

logger = logging.getLogger("MajestyGuard.rPPG")

class CHROMrPPGDetector:
    MIN_FRAMES = 45; WINDOW_FRAMES = 90; FPS = 15.0
    FREQ_LO = 0.67; FREQ_HI = 4.00

    def __init__(self):
        self._rgb_buf: deque = deque(maxlen=self.WINDOW_FRAMES)
        self._score: float = 0.5

    def reset(self): self._rgb_buf.clear(); self._score = 0.5

    @property
    def score(self) -> float: return self._score

    @property
    def has_signal(self) -> bool: return len(self._rgb_buf) >= self.MIN_FRAMES

    def update(self, frame: np.ndarray, face: Any) -> float:
        if not _SCIPY_OK: return 0.5
        roi = self._skin_roi(frame, face)
        if roi is None or roi.size == 0: return self._score
        b, g, r = cv2.split(roi)
        R, G, B = float(np.mean(r)), float(np.mean(g)), float(np.mean(b))
        if R < 15 or G < 15: return self._score
        self._rgb_buf.append((R, G, B))
        if not self.has_signal: return 0.5
        self._score = self._chrom()
        return self._score

    def _chrom(self) -> float:
        frames = np.array(self._rgb_buf, dtype=np.float64)
        R, G, B = frames[:,0], frames[:,1], frames[:,2]
        Rn = R/(np.mean(R)+1e-9); Gn = G/(np.mean(G)+1e-9); Bn = B/(np.mean(B)+1e-9)
        Xs = 3*Rn - 2*Gn; Ys = 1.5*Rn + Gn - 1.5*Bn
        H  = Xs - (np.std(Xs)+1e-9)/(np.std(Ys)+1e-9) * Ys
        nyq = self.FPS/2.0
        try:
            b,a = scipy_signal.butter(4,[self.FREQ_LO/nyq, min(self.FREQ_HI/nyq,0.98)],btype='band')
            H = scipy_signal.filtfilt(b,a,H)
        except: return 0.5
        N = len(H)
        fft = np.abs(np.fft.rfft(H, n=N*2))
        freqs = np.fft.rfftfreq(N*2, d=1.0/self.FPS)
        mask = (freqs >= self.FREQ_LO) & (freqs <= self.FREQ_HI)
        if mask.sum() < 2: return 0.5
        snr = float(np.max(fft[mask])) / (float(np.mean(fft[mask]))+1e-9)
        score = float(np.clip((snr-1.3)/(3.5-1.3), 0.0, 1.0))
        logger.debug("rPPG SNR=%.2f → %.3f", snr, score)
        return score

    def _skin_roi(self, frame, face):
        try:
            x1,y1,x2,y2 = [int(v) for v in face.bbox]
            fh,fw = y2-y1, x2-x1
            roi = frame[max(0,y1+int(fh*0.05)):y1+int(fh*0.35),
                        max(0,x1+int(fw*0.20)):x2-int(fw*0.20)]
            return roi if roi.size > 0 else None
        except: return None
