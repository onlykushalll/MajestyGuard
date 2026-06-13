import pytest
import numpy as np
from types import SimpleNamespace
from attention_detector import AttentionDetector

class MockLandmark:
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

def make_mock_landmarks(eye_h_width: float, iris_x_offset: float = 0.0, iris_y_offset: float = 0.0, ear_y_val: float = 0.25):
    # Left Eye Horizontal coordinates: 33 and 133
    # Right Eye Horizontal coordinates: 362 and 263
    # Left Iris coordinates: 468, 469, 470, 471
    # Right Iris coordinates: 472, 473, 474, 475
    # Left Eye Vertical: (159, 145), (158, 153)
    # Right Eye Vertical: (386, 374), (385, 380)

    landmarks = [MockLandmark(0.0, 0.0) for _ in range(476)]

    # Left eye horizontal width
    landmarks[33] = MockLandmark(0.10, 0.50)
    landmarks[133] = MockLandmark(0.10 + eye_h_width, 0.50)

    # Right eye horizontal width
    landmarks[362] = MockLandmark(0.50, 0.50)
    landmarks[263] = MockLandmark(0.50 + eye_h_width, 0.50)

    # Left Iris Center (around eye center)
    left_center_x = 0.10 + eye_h_width / 2.0 + iris_x_offset
    left_center_y = 0.50 + iris_y_offset
    for idx in [468, 469, 470, 471]:
        landmarks[idx] = MockLandmark(left_center_x, left_center_y)

    # Right Iris Center
    right_center_x = 0.50 + eye_h_width / 2.0 + iris_x_offset
    right_center_y = 0.50 + iris_y_offset
    for idx in [472, 473, 474, 475]:
        landmarks[idx] = MockLandmark(right_center_x, right_center_y)

    # Left eye vertical coordinates
    v_diff = ear_y_val * eye_h_width
    landmarks[159] = MockLandmark(0.15, 0.50 - v_diff / 2.0)
    landmarks[145] = MockLandmark(0.15, 0.50 + v_diff / 2.0)
    landmarks[158] = MockLandmark(0.15, 0.50 - v_diff / 2.0)
    landmarks[153] = MockLandmark(0.15, 0.50 + v_diff / 2.0)

    # Right eye vertical coordinates
    landmarks[386] = MockLandmark(0.55, 0.50 - v_diff / 2.0)
    landmarks[374] = MockLandmark(0.55, 0.50 + v_diff / 2.0)
    landmarks[385] = MockLandmark(0.55, 0.50 - v_diff / 2.0)
    landmarks[380] = MockLandmark(0.55, 0.50 + v_diff / 2.0)

    return landmarks

class MockMeshResult:
    def __init__(self, landmarks=None):
        if landmarks:
            self.multi_face_landmarks = [SimpleNamespace(landmark=landmarks)]
        else:
            self.multi_face_landmarks = None

class MockMesh:
    def __init__(self):
        self.result = MockMeshResult()
        self.closed = False

    def process(self, rgb):
        return self.result

    def close(self):
        self.closed = True

def test_attention_detector_not_ready():
    detector = AttentionDetector()
    detector._ready = False
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert detector.score(frame) == 0.5

def test_attention_detector_no_face(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    mock_mesh.result = MockMeshResult(landmarks=None)
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert detector.score(frame) == 0.5

def test_attention_detector_insufficient_history(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    
    for _ in range(7):
        mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=0.0))
        assert detector.score(frame) == 0.5

def test_attention_detector_still_eyes_spoof(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # 10 frames of identical iris positions (perfectly still, std = 0)
    for _ in range(10):
        mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=0.0))
        score = detector.score(frame)
    
    assert score == pytest.approx(0.29, abs=1e-3)

def test_attention_detector_moving_eyes_live(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Alternating offset of 0.002 on eye_h_width = 0.05.
    # Normalized x varies by 0.002 / 0.05 = 0.04.
    # std of [0, 0.04, 0, 0.04, ...] is 0.02.
    # Alternating y offset of 0.002 on eye_h_width = 0.05.
    # Normalized y varies by 0.04, std is 0.02.
    # Mean of stds is 0.02 > _MIN_STD_LIVE (0.008).
    # variability_score = 1.0.
    for i in range(10):
        offset = 0.002 if (i % 2 == 0) else 0.0
        mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=offset, iris_y_offset=offset))
        score = detector.score(frame)

    assert score == pytest.approx(0.85, abs=1e-3)

def test_attention_detector_blink_detection(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    for i in range(45):
        offset = 0.002 if (i % 2 == 0) else 0.0
        ear = 0.10 if i == 20 else 0.25
        mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=offset, iris_y_offset=offset, ear_y_val=ear))
        score = detector.score(frame)

    assert detector._blink_count == 1
    assert score == pytest.approx(1.0, abs=1e-3)

def test_attention_detector_no_blinks_suspicion(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    for i in range(45):
        offset = 0.002 if (i % 2 == 0) else 0.0
        mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=offset, iris_y_offset=offset, ear_y_val=0.25))
        score = detector.score(frame)

    assert detector._blink_count == 0
    assert score == pytest.approx(0.82, abs=1e-3)

def test_attention_detector_reset_and_close(monkeypatch):
    detector = AttentionDetector()
    mock_mesh = MockMesh()
    detector._mesh = mock_mesh
    detector._ready = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    mock_mesh.result = MockMeshResult(landmarks=make_mock_landmarks(eye_h_width=0.05, iris_x_offset=0.0))
    detector.score(frame)
    assert len(detector._iris_x_hist) == 1

    detector.reset()
    assert len(detector._iris_x_hist) == 0
    assert detector._blink_count == 0

    detector.close()
    assert mock_mesh.closed
    assert detector._mesh is None
    assert not detector._ready
