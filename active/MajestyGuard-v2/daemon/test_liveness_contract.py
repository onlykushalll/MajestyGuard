import inspect
from collections import deque
from types import SimpleNamespace

import numpy as np

from liveness_detector import LivenessDetector


class FakeFace:
    def __init__(self, bbox):
        self.bbox = bbox


def test_liveness_contract_keeps_window_and_percentile():
    assert LivenessDetector._WINDOW == 30
    source = inspect.getsource(LivenessDetector.score_full)
    assert "np.percentile(window, 10)" in source
    assert "np.mean(self._score_history)" in source


def test_liveness_contract_keeps_minifasnet_real_index_zero():
    source = inspect.getsource(LivenessDetector._onnx_antispoof_score)
    assert "real_prob = float(probs[0])" in source


def test_layer_diagnostic_summary_line_includes_mean_for_gate_parser():
    from mg_layers3 import _summary_stats_line

    line = _summary_stats_line("onnx", np.array([0.90, 0.95, 1.00], dtype=np.float32))

    assert line == "  onnx       median=0.950 p10=0.910 p90=0.990 mean=0.950"


def test_liveness_roi_uses_square_128_crop_at_frame_edge():
    detector = LivenessDetector.__new__(LivenessDetector)
    frame = np.zeros((96, 128, 3), dtype=np.uint8)
    frame[:, :, 0] = np.arange(128, dtype=np.uint8)
    frame[:, :, 1] = np.arange(96, dtype=np.uint8)[:, None]

    roi = LivenessDetector._extract_roi(detector, frame, FakeFace([0, 8, 32, 64]))

    assert roi is not None
    assert roi.shape == (128, 128, 3)
    assert roi.dtype == np.uint8


def test_liveness_skips_low_quality_faces_without_poisoning_window():
    detector = LivenessDetector.__new__(LivenessDetector)
    detector._score_history = deque([0.82, 0.84], maxlen=LivenessDetector._WINDOW)
    detector._frame_index = 2
    detector._last_smoothed_score = 0.83
    detector._depth_liveness = None
    detector._rppg = SimpleNamespace(has_signal=False, update=lambda frame, face: 0.5)
    detector._attention = SimpleNamespace(score=lambda frame: 0.5)
    detector._last_onnx_idx0 = float("nan")
    detector._last_onnx_idx1 = float("nan")

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

    low_quality_frame = np.zeros((120, 160, 3), dtype=np.uint8)
    score = detector.score(low_quality_frame, FakeFace([20, 20, 24, 24]))

    assert score == 0.83
    assert list(detector._score_history) == [0.82, 0.84]
    assert detector._frame_index == 2
