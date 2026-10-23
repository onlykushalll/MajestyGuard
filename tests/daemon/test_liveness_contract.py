import inspect
from collections import deque
from types import SimpleNamespace
import time

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
    detector._last_smoothed_score_time = time.monotonic()
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


def test_liveness_stale_cached_score_fails_closed_instead_of_persisting():
    """H16 regression guard.

    score_full() falls back to the last smoothed score on a low-quality
    frame. Without a staleness cap, that fallback could return the same
    value indefinitely under sustained poor conditions. Once the cached
    score is older than _STALE_SCORE_MAX_AGE_S it must return 0.0 (fail
    closed), not the stale cached value.
    """
    detector = LivenessDetector.__new__(LivenessDetector)
    detector._score_history = deque([0.82, 0.84], maxlen=LivenessDetector._WINDOW)
    detector._frame_index = 2
    detector._last_smoothed_score = 0.83
    # Older than _STALE_SCORE_MAX_AGE_S (4.0s) — must not be trusted.
    detector._last_smoothed_score_time = (
        time.monotonic() - LivenessDetector._STALE_SCORE_MAX_AGE_S - 1.0
    )
    detector._depth_liveness = None
    detector._rppg = SimpleNamespace(has_signal=False, update=lambda frame, face: 0.5)
    detector._attention = SimpleNamespace(score=lambda frame: 0.5)
    detector._last_onnx_idx0 = float("nan")
    detector._last_onnx_idx1 = float("nan")

    low_quality_frame = np.zeros((120, 160, 3), dtype=np.uint8)
    score = detector.score(low_quality_frame, FakeFace([20, 20, 24, 24]))

    assert score == 0.0, "stale cached liveness score must fail closed, not persist"


def test_replay_taint_persists_across_reset_temporal_buffers_and_clears_on_reset_session():
    detector = LivenessDetector.__new__(LivenessDetector)
    detector._score_history = deque(maxlen=LivenessDetector._WINDOW)
    detector._frame_index = 0
    detector._last_smoothed_score = 0.0
    detector._last_smoothed_score_time = 0.0
    detector._eye_brightness_history = deque(maxlen=30)
    detector._blink_count = 0
    detector._blink_cooldown = 0
    detector._last_blink_frame = 0
    detector._in_blink = False
    detector._face_center_history = deque(maxlen=30)
    detector._frame_hashes = deque(maxlen=300)
    detector._duplicate_frame_count = 0
    detector._replay_flagged_at = None
    detector._replay_taint_decay_seconds = 300.0
    detector._landmark_ratios = deque(maxlen=30)
    detector._hist_history = deque(maxlen=15)
    detector._onnx_score_history = deque(maxlen=5)
    detector._depth_liveness = None
    detector._rppg = None
    detector._attention = None
    detector._onnx_consecutive_failures = 0

    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    # Feed identical frames to trigger duplicate frame detection
    penalty = 1.0
    for _ in range(7):
        penalty = detector._replay_detection(roi)

    assert penalty < 0.3
    detector._replay_flagged_at = time.monotonic()

    assert detector.replay_flagged_this_session() is True
    assert len(detector._frame_hashes) > 0
    assert detector._duplicate_frame_count > 0

    # Calling reset_temporal_buffers must preserve replay taint and frame hashes
    detector.reset_temporal_buffers()
    assert detector.replay_flagged_this_session() is True
    assert len(detector._frame_hashes) > 0
    assert detector._duplicate_frame_count > 0

    # Calling reset_session must clear replay taint and frame hashes
    detector.reset_session()
    assert detector.replay_flagged_this_session() is False
    assert len(detector._frame_hashes) == 0
    assert detector._duplicate_frame_count == 0


def test_replay_taint_decays_after_configured_window(monkeypatch):
    detector = LivenessDetector.__new__(LivenessDetector)
    detector._replay_flagged_at = 100.0
    detector._replay_taint_decay_seconds = 300.0

    monkeypatch.setattr(time, "monotonic", lambda: 100.0 + detector._replay_taint_decay_seconds - 1.0)
    assert detector.replay_flagged_this_session() is True

    monkeypatch.setattr(time, "monotonic", lambda: 100.0 + detector._replay_taint_decay_seconds + 1.0)
    assert detector.replay_flagged_this_session() is False



def test_liveness_deferred_heavy_models_load_on_demand_in_score_full(monkeypatch):
    """When defer_heavy_models=True, __init__ must not instantiate DepthLivenessDetector
    or AttentionDetector until _ensure_heavy_models_loaded / score_full runs."""
    depth_calls = []
    attn_calls = []
    monkeypatch.setattr(
        "liveness_detector.DepthLivenessDetector",
        lambda model_dir: depth_calls.append(model_dir) or SimpleNamespace(available=False, score=lambda f, face: 0.5),
    )
    monkeypatch.setattr(
        "liveness_detector.AttentionDetector",
        lambda: attn_calls.append(True) or SimpleNamespace(score=lambda f: 0.5, close=lambda: None),
    )

    det = LivenessDetector(model_dir="", defer_heavy_models=True)
    assert det._heavy_models_loaded is False
    assert depth_calls == []
    assert attn_calls == []

    det._ensure_heavy_models_loaded()
    assert det._heavy_models_loaded is True
    assert len(attn_calls) == 1
