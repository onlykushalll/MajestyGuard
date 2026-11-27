import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import rppg_detector
from liveness_detector import LivenessDetector
from rppg_detector import CHROMrPPGDetector


def test_rppg_detector_does_not_use_bare_except():
    tree = ast.parse(Path(rppg_detector.__file__).read_text(encoding="utf-8"))

    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]

    assert handlers == []


def test_skin_roi_returns_none_for_invalid_face_box():
    detector = CHROMrPPGDetector()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=None)

    assert detector._skin_roi(frame, face) is None


def test_chrom_returns_neutral_when_filter_fails(monkeypatch):
    detector = CHROMrPPGDetector()
    detector._snr = 2.5
    detector._rgb_buf.extend([(60.0 + i, 55.0 + i, 50.0 + i) for i in range(detector.MIN_FRAMES)])

    def fail_filter(*_args, **_kwargs):
        raise ValueError("bad filter")

    monkeypatch.setattr(rppg_detector.scipy_signal, "butter", fail_filter)

    assert detector._chrom() == 0.5
    assert detector.snr == 0.0


def test_rppg_update_guards_shape_and_resets_score_on_dark_or_missing_roi():
    detector = CHROMrPPGDetector()
    detector._score = 0.85
    face = SimpleNamespace(bbox=[0, 0, 100, 100])

    # Grayscale frame (2D ROI) must not crash cv2.split and must reset score
    gray_frame = np.full((120, 120), 100, dtype=np.uint8)
    assert detector.update(gray_frame, face) == 0.5

    detector._score = 0.85
    # Dark BGR frame must not return stale score
    dark_frame = np.full((120, 120, 3), 5, dtype=np.uint8)
    assert detector.update(dark_frame, face) == 0.5

    detector._score = 0.85
    # Signal loss (no face box) must not return stale score
    assert detector.update(dark_frame, SimpleNamespace(bbox=None)) == 0.5


def test_chrom_resets_snr_when_band_mask_has_insufficient_bins():
    detector = CHROMrPPGDetector()
    detector._snr = 3.1
    detector._rgb_buf.extend([(60.0, 55.0, 50.0) for _ in range(detector.MIN_FRAMES)])
    detector.FREQ_LO = 100.0
    detector.FREQ_HI = 101.0

    assert detector._chrom() == 0.5
    assert detector.snr == 0.0


def test_rppg_confident_no_pulse_snr_veto_heavily_penalizes_liveness():
    def make_detector(rppg_snr, rppg_score=0.0):
        detector = LivenessDetector.__new__(LivenessDetector)
        detector._score_history = deque(maxlen=LivenessDetector._WINDOW)
        detector._frame_index = 10
        detector._last_smoothed_score = 0.0
        detector._last_smoothed_score_time = 0.0
        detector._rppg_confident_no_pulse_snr = 1.15
        detector._depth_liveness = None
        detector._rppg = SimpleNamespace(has_signal=True, snr=rppg_snr, update=lambda frame, face: rppg_score)
        detector._attention = SimpleNamespace(score=lambda frame: 0.0)
        detector._last_onnx_idx0 = float("nan")
        detector._last_onnx_idx1 = float("nan")
        detector._onnx_score_history = deque(maxlen=5)

        detector._extract_roi = lambda frame, face: np.zeros((128, 128, 3), dtype=np.uint8)
        detector._replay_detection = lambda roi: 0.95
        detector._lbp_texture_score = lambda roi: 0.80
        detector._specular_score = lambda roi: 0.80
        detector._color_space_score = lambda roi: 0.80
        detector._moire_score = lambda roi: 0.80
        detector._temporal_blink_score = lambda frame, face: 0.80
        detector._boundary_score = lambda frame, face: 0.80
        detector._onnx_antispoof_score = lambda roi: 0.80
        detector._depth_geometry_score = lambda face: 0.80
        detector._histogram_consistency_score = lambda roi: 0.80
        return detector

    face = SimpleNamespace(bbox=[10, 10, 100, 100], kps=None)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)

    det_veto = make_detector(rppg_snr=1.05, rppg_score=0.0)
    score_veto = det_veto.score_full(frame, face)

    det_soft = make_detector(rppg_snr=1.25, rppg_score=0.0)
    score_soft = det_soft.score_full(frame, face)

    assert score_soft - score_veto >= 0.15


def test_rppg_effective_fps_uses_measured_monotonic_timestamps():
    detector = CHROMrPPGDetector()

    assert detector._effective_fps() == 15.0

    dt = 1.0 / 30.0
    for i in range(30):
        detector._ts_buf.append(i * dt)
    assert detector._effective_fps() == pytest.approx(30.0, rel=1e-2)

    detector._ts_buf.clear()
    dt_fast = 1.0 / 100.0
    for i in range(30):
        detector._ts_buf.append(i * dt_fast)
    assert detector._effective_fps() == detector.FPS_MAX

    detector._ts_buf.clear()
    dt_slow = 1.0 / 2.0
    for i in range(30):
        detector._ts_buf.append(i * dt_slow)
    assert detector._effective_fps() == detector.FPS_MIN
