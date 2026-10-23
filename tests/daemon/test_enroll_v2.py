from types import SimpleNamespace
import numpy as np

import enroll_v2


def test_face_readiness_adapts_distance_and_offset_for_posture_angles():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    # Add high-frequency checkerboard in face ROI so sharpness is high
    frame[140:300, 220:420:2] = 200
    face = SimpleNamespace(
        bbox=[240.0, 160.0, 400.0, 304.0],  # height_frac = 144 / 480 = 0.30
        det_score=0.92,
        normed_embedding=np.ones(512, dtype=np.float32),
    )
    ready_front, status_front, _ = enroll_v2._face_readiness(
        frame, face, min_quality=0.50, angle="Front - neutral expression"
    )
    assert ready_front is False
    assert status_front == "move closer"

    ready_lean, status_lean, _ = enroll_v2._face_readiness(
        frame, face, min_quality=0.50, angle="Leaning back / away - relaxed real working posture"
    )
    assert ready_lean is True
    assert status_lean == "READY"
