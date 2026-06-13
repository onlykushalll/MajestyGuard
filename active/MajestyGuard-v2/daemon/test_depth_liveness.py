import pytest
import numpy as np
import os
from types import SimpleNamespace
from depth_liveness import DepthLivenessDetector

class MockInput:
    def __init__(self, name):
        self.name = name

class MockInferenceSession:
    def __init__(self, model_path, **kwargs):
        self.model_path = model_path
        self.outputs = [np.ones((1, 256, 256), dtype=np.float32)]
        self.raise_exception = False

    def get_inputs(self):
        return [MockInput("input")]

    def run(self, output_names, input_feed):
        if self.raise_exception:
            raise RuntimeError("Mock ONNX runtime error")
        return self.outputs

def test_depth_liveness_not_available(monkeypatch):
    # Ensure os.path.exists returns False for the model path
    monkeypatch.setattr(os.path, "exists", lambda path: False)
    detector = DepthLivenessDetector("dummy_dir")
    assert not detector.available
    
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    assert detector.score(frame, face) == 0.5

def test_depth_liveness_face_crop_too_small(monkeypatch):
    # Mock model exists but face is too small
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    import onnxruntime as ort
    monkeypatch.setattr(ort, "InferenceSession", MockInferenceSession)

    detector = DepthLivenessDetector("dummy_dir")
    assert detector.available

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Face bounding box is 100 to 120 (width/height is 20, which is < 32)
    face = SimpleNamespace(bbox=[100, 100, 120, 120])
    assert detector.score(frame, face) == 0.5

def test_depth_liveness_inference_exception(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    import onnxruntime as ort
    monkeypatch.setattr(ort, "InferenceSession", MockInferenceSession)

    detector = DepthLivenessDetector("dummy_dir")
    # Set the mock session to raise exception
    detector._session.raise_exception = True

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    assert detector.score(frame, face) == 0.5

def test_depth_liveness_flat_depth_spoof(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    import onnxruntime as ort
    monkeypatch.setattr(ort, "InferenceSession", MockInferenceSession)

    detector = DepthLivenessDetector("dummy_dir")
    
    # Flat depth map (all 1.0s or constant values)
    # This will trigger: mx - mn < 1e-6 -> returns None for depth map -> returns 0.5
    detector._session.outputs = [np.ones((1, 256, 256), dtype=np.float32) * 5.0]
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    assert detector.score(frame, face) == 0.5

    # Near-flat depth map (very small variance)
    # We want depth range to be > 1e-6 but very flat (e.g. CV < 0.04)
    # Let's construct a depth map with range [0.0, 1.0] but almost entirely 0.5, except one pixel
    depth = np.ones((256, 256), dtype=np.float32) * 0.5
    depth[0, 0] = 0.0
    depth[255, 255] = 1.0
    detector._session.outputs = [depth[np.newaxis]]

    score = detector.score(frame, face)
    # Flat image should yield a very low score
    assert score <= 0.35

def test_depth_liveness_3d_depth_real(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    import onnxruntime as ort
    monkeypatch.setattr(ort, "InferenceSession", MockInferenceSession)

    detector = DepthLivenessDetector("dummy_dir")

    # Let's construct a depth map representing a real 3D face structure.
    # Center cell (nose) should be closer (higher depth value, e.g. 0.9).
    # Corner cells (background/ears) should be further (lower depth value, e.g. 0.1).
    depth = np.zeros((256, 256), dtype=np.float32)
    # 3x3 grid size is cw = 256 // 3 = 85.
    # Nose cell is row 1, col 1 (coordinates 85 to 170).
    depth[85:170, 85:170] = 0.95
    # Corners are further
    depth[0:85, 0:85] = 0.1
    depth[0:85, 170:256] = 0.1
    depth[170:256, 0:85] = 0.1
    depth[170:256, 170:256] = 0.1
    # Rest are intermediate
    depth[85:170, 0:85] = 0.4
    depth[85:170, 170:256] = 0.4
    depth[0:85, 85:170] = 0.4
    depth[170:256, 85:170] = 0.4

    detector._session.outputs = [depth[np.newaxis]]
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200])

    score = detector.score(frame, face)
    # Should yield a high score indicating a real face
    assert score >= 0.70
