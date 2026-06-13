import cv2
import numpy as np

from face_quality import measure_face_quality


class FakeFace:
    def __init__(self, bbox, det_score=0.90):
        self.bbox = np.array(bbox, dtype=np.float32)
        self.det_score = det_score


def _checkerboard_frame(value_a=32, value_b=224):
    frame = np.full((240, 320, 3), 128, dtype=np.uint8)
    yy, xx = np.indices((160, 160))
    board = np.where(((xx // 4) + (yy // 4)) % 2 == 0, value_a, value_b).astype(np.uint8)
    frame[40:200, 80:240] = cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)
    return frame


def test_face_quality_rewards_sharp_faces():
    face = FakeFace([80, 40, 240, 200])
    sharp = _checkerboard_frame()
    blurred = cv2.GaussianBlur(sharp, (31, 31), 0)

    sharp_quality = measure_face_quality(sharp, face)
    blurred_quality = measure_face_quality(blurred, face)

    assert sharp_quality.sharp_score > blurred_quality.sharp_score
    assert sharp_quality.score > blurred_quality.score


def test_face_quality_penalizes_poor_illumination():
    face = FakeFace([80, 40, 240, 200])
    neutral = np.full((240, 320, 3), 128, dtype=np.uint8)
    dark = np.full((240, 320, 3), 16, dtype=np.uint8)

    neutral_quality = measure_face_quality(neutral, face)
    dark_quality = measure_face_quality(dark, face)

    assert neutral_quality.illumination_score > dark_quality.illumination_score
    assert neutral_quality.score > dark_quality.score


def test_face_quality_reports_center_offset():
    centered = measure_face_quality(np.full((240, 320, 3), 128, dtype=np.uint8), FakeFace([80, 40, 240, 200]))
    off_center = measure_face_quality(np.full((240, 320, 3), 128, dtype=np.uint8), FakeFace([0, 20, 120, 180]))

    assert centered.center_offset < off_center.center_offset
