from types import SimpleNamespace

import numpy as np
import pytest

from liveness_detector import LivenessDetector


def _face():
    return SimpleNamespace(
        bbox=np.array([24, 24, 120, 132], dtype=np.float32),
        kps=np.array(
            [
                [50, 60],
                [94, 60],
                [72, 82],
                [55, 108],
                [90, 108],
            ],
            dtype=np.float32,
        ),
    )


def _detector(monkeypatch):
    detector = LivenessDetector.__new__(LivenessDetector)
    detector._score_history = []
    detector._frame_index = 0
    detector._last_smoothed_score = 0.0
    detector._rppg = SimpleNamespace(
        has_signal=False,
        calls=0,
        update=lambda frame, face: setattr(detector._rppg, "calls", detector._rppg.calls + 1) or 0.5,
        reset=lambda: None,
    )
    detector._attention = SimpleNamespace(score=lambda frame: 0.8, close=lambda: None)
    detector._depth_liveness = SimpleNamespace(available=True, score=lambda frame, face: 0.8)
    detector._onnx_score_history = []
    detector._frame_hashes = []
    detector._duplicate_frame_count = 0
    detector._eye_brightness_history = []
    detector._blink_count = 0
    detector._blink_cooldown = 0
    detector._last_blink_frame = 0
    detector._in_blink = False
    detector._face_center_history = []
    detector._landmark_ratios = []
    detector._hist_history = []
    detector._antispoof_session = None
    detector._last_onnx_idx0 = float("nan")
    detector._last_onnx_idx1 = float("nan")
    detector._onnx_consecutive_failures = 0

    monkeypatch.setattr("liveness_detector.measure_face_quality", lambda frame, face: SimpleNamespace(
        score=0.9,
        sharpness=100.0,
        illumination_mean=128.0,
        height_frac=0.5,
        center_offset=0.1,
    ))
    monkeypatch.setattr(detector, "_extract_roi", lambda frame, face: np.full((128, 128, 3), 128, dtype=np.uint8))
    monkeypatch.setattr(detector, "_replay_detection", lambda roi: 0.9)
    monkeypatch.setattr(detector, "_lbp_texture_score", lambda roi: 0.9)
    monkeypatch.setattr(detector, "_specular_score", lambda roi: 0.85)
    monkeypatch.setattr(detector, "_color_space_score", lambda roi: 0.9)
    monkeypatch.setattr(detector, "_moire_score", lambda roi: 0.9)
    monkeypatch.setattr(detector, "_temporal_blink_score", lambda frame, face: 0.86)
    monkeypatch.setattr(detector, "_boundary_score", lambda frame, face: 0.9)
    monkeypatch.setattr(detector, "_onnx_antispoof_score", lambda roi: 0.92)
    monkeypatch.setattr(detector, "_depth_geometry_score", lambda face: 0.84)
    monkeypatch.setattr(detector, "_histogram_consistency_score", lambda roi: 0.8)
    return detector


def test_score_fast_does_not_call_rppg_or_attention(monkeypatch):
    detector = _detector(monkeypatch)
    frame = np.full((160, 160, 3), 128, dtype=np.uint8)

    score = detector.score_fast(frame, _face())

    assert score >= 0.82
    assert detector._rppg.calls == 0


def test_score_full_calls_temporal_rppg_and_attention(monkeypatch):
    detector = _detector(monkeypatch)
    frame = np.full((160, 160, 3), 128, dtype=np.uint8)

    score = detector.score_full(frame, _face())

    assert score >= 0.70
    assert detector._rppg.calls == 1


def test_score_alias_uses_full_path(monkeypatch):
    detector = _detector(monkeypatch)
    frame = np.full((160, 160, 3), 128, dtype=np.uint8)

    assert detector.score(frame, _face()) == pytest.approx(detector._last_smoothed_score)
    assert detector._rppg.calls == 1
