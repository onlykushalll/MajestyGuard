from types import SimpleNamespace

import numpy as np

from diagnostic_common import enhance_frame, posture_label, select_primary_face


def test_posture_label_normalizes_human_descriptions():
    assert posture_label(None) == "unspecified"
    assert posture_label("") == "unspecified"
    assert posture_label(" leaning back / away ") == "leaning-back-away"


def test_select_primary_face_prefers_centered_foreground_face():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    tiny_high_confidence = SimpleNamespace(bbox=[12, 20, 62, 70], det_score=0.99)
    centered_user = SimpleNamespace(bbox=[180, 100, 460, 420], det_score=0.82)

    selected = select_primary_face(frame, [tiny_high_confidence, centered_user])

    assert selected is centered_user


def test_select_primary_face_rejects_invalid_boxes():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    invalid = SimpleNamespace(bbox=[10, 10, 10, 50], det_score=0.99)

    assert select_primary_face(frame, [invalid]) is None


def test_enhance_frame_returns_frame_shape_and_dtype():
    frame = np.full((32, 32, 3), 20, dtype=np.uint8)

    enhanced = enhance_frame(frame)

    assert enhanced.shape == frame.shape
    assert enhanced.dtype == frame.dtype
