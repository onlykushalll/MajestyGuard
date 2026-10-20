"""tests/daemon/test_offline_replay_harness.py — Tests for offline replay & soak harness."""
from collections import deque
from types import SimpleNamespace
import numpy as np
import pytest

from offline_replay_harness import OfflineReplayHarness, SoakMetrics
from liveness_detector import LivenessDetector


def test_harness_frame_generator_patterns():
    frames = list(OfflineReplayHarness.generate_synthetic_frames(5, width=64, height=48, pattern="static"))
    assert len(frames) == 5
    assert frames[0].shape == (48, 64, 3)
    # Static pattern produces identical arrays
    assert np.array_equal(frames[0], frames[1])

    noise_frames = list(OfflineReplayHarness.generate_synthetic_frames(3, width=64, height=48, pattern="noise", noise_std=5.0))
    assert len(noise_frames) == 3
    # Noise pattern produces differing arrays
    assert not np.array_equal(noise_frames[0], noise_frames[1])

    pulse_frames = list(OfflineReplayHarness.generate_synthetic_frames(3, width=64, height=48, pattern="pulse"))
    assert len(pulse_frames) == 3


def test_harness_500_frame_soak_verifies_bounded_memory_and_replay():
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
    detector._landmark_ratios = deque(maxlen=20)
    detector._hist_history = deque(maxlen=15)
    detector._onnx_score_history = deque(maxlen=5)
    detector._depth_liveness = None
    detector._rppg = None
    detector._attention = None
    detector._onnx_consecutive_failures = 0

    # Mock per-frame extractors so we can run 500 frames purely offline without camera or weights
    detector._extract_roi = lambda frame, face: frame[10:50, 10:50].copy()
    detector._lbp_texture_score = lambda roi: 0.80
    detector._specular_score = lambda roi: 0.80
    detector._color_space_score = lambda roi: 0.80
    detector._moire_score = lambda roi: 0.80
    detector._temporal_blink_score = lambda frame, face: 0.80
    detector._boundary_score = lambda frame, face: 0.80
    detector._onnx_antispoof_score = lambda roi: 0.80
    detector._depth_geometry_score = lambda face: 0.80
    detector._histogram_consistency_score = lambda roi: 0.80

    harness = OfflineReplayHarness(detector)

    face = SimpleNamespace(bbox=[10, 10, 50, 50], kps=None)
    frames = harness.generate_synthetic_frames(500, width=64, height=64, pattern="static")

    metrics = harness.run_soak(frames, face_factory=lambda _: face)

    assert metrics.total_frames == 500
    # Over 500 static frames, replay must be flagged
    assert metrics.replay_flagged_count > 450
    assert detector.replay_flagged_this_session() is True

    # Memory bounding assertion: verify deques stayed within their maxlens
    assert metrics.max_deque_sizes["_frame_hashes"] <= 300
    assert metrics.max_deque_sizes["_score_history"] <= LivenessDetector._WINDOW
    assert len(detector._frame_hashes) == 300

    # Reset temporal buffers retains replay flag
    detector.reset_temporal_buffers()
    assert detector.replay_flagged_this_session() is True
    assert len(detector._frame_hashes) == 300

    # Reset session completely clears replay flag and hashes
    detector.reset_session()
    assert detector.replay_flagged_this_session() is False
    assert len(detector._frame_hashes) == 0
