import numpy as np
import pytest
from types import SimpleNamespace

import face_engine
from face_engine import FaceEngine


class _FakeFace:
    def __init__(self, bbox=None, kps=None):
        self.bbox = np.array(bbox or [20, 30, 120, 150], dtype=np.float32)
        if kps is not None:
            self.kps = np.array(kps, dtype=np.float32)
        self.normed_embedding = np.ones(512, dtype=np.float32)
        self.normed_embedding /= np.linalg.norm(self.normed_embedding)


def test_adaface_searches_auxiliary_models_before_insightface_root(monkeypatch):
    engine = FaceEngine.__new__(FaceEngine)
    engine.model_dir = r"C:\models_insightface"
    engine._aux_model_dir = r"C:\models"

    existing = {r"C:\models\adaface_r100.onnx"}
    monkeypatch.setattr(face_engine.os.path, "exists", lambda path: path in existing)

    assert engine._find_adaface_model_path() == r"C:\models\adaface_r100.onnx"


class _FakeInput:
    name = "data"


class _FakeSession:
    def __init__(self):
        self.feeds = []

    def get_inputs(self):
        return [_FakeInput()]

    def run(self, _outputs, feed):
        self.feeds.append(feed)
        base = np.ones(512, dtype=np.float32)
        base[0] = float(len(self.feeds))
        return [base.reshape(1, 512)]

    @property
    def feed(self):
        return self.feeds[-1]


def test_adaface_embedding_uses_detected_input_name():
    engine = FaceEngine.__new__(FaceEngine)
    session = _FakeSession()
    engine._adaface_session = session
    engine._adaface_input_name = "data"
    face_roi = np.full((112, 112, 3), 128, dtype=np.uint8)

    embedding = engine._get_embedding_adaface(face_roi)

    assert "data" in session.feed
    assert embedding.shape == (512,)
    assert np.linalg.norm(embedding) == pytest.approx(1.0)


def test_adaface_face_chip_uses_five_landmark_alignment():
    engine = FaceEngine.__new__(FaceEngine)
    frame = np.zeros((160, 160, 3), dtype=np.uint8)
    for y in range(160):
        for x in range(160):
            frame[y, x] = (x % 256, y % 256, (x + y) % 256)
    face = _FakeFace(
        bbox=[20, 30, 120, 150],
        kps=[
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ],
    )

    chip = engine._extract_adaface_chip(frame, face)

    assert chip.shape == (112, 112, 3)
    assert np.any(chip != 0)
    assert chip[52, 38, 0] == pytest.approx(frame[52, 38, 0], abs=3)


def test_adaface_face_chip_falls_back_to_bbox_when_landmarks_are_missing():
    engine = FaceEngine.__new__(FaceEngine)
    frame = np.zeros((160, 160, 3), dtype=np.uint8)
    frame[30:150, 20:120, 1] = 200
    face = _FakeFace(bbox=[20, 30, 120, 150])

    chip = engine._extract_adaface_chip(frame, face)

    assert chip.shape == (112, 112, 3)
    assert chip[:, :, 1].mean() > 150


def test_adaface_embedding_preserves_bgr_channel_order():
    engine = FaceEngine.__new__(FaceEngine)
    session = _FakeSession()
    engine._adaface_session = session
    engine._adaface_input_name = "data"
    face_roi = np.zeros((112, 112, 3), dtype=np.uint8)
    face_roi[:, :, 0] = 255

    engine._get_embedding_adaface(face_roi)
    blob = session.feed["data"]

    assert blob[0, 0, 0, 0] > 0.99
    assert blob[0, 2, 0, 0] < -0.99


def test_adaface_embedding_fuses_original_and_flipped_features_by_default():
    engine = FaceEngine.__new__(FaceEngine)
    session = _FakeSession()
    engine._adaface_session = session
    engine._adaface_input_name = "data"
    engine._adaface_flip_fusion_enabled = True
    face_roi = np.zeros((112, 112, 3), dtype=np.uint8)
    face_roi[:, :56, 0] = 255

    embedding = engine._get_embedding_adaface(face_roi)

    assert len(session.feeds) == 2
    first_blob = session.feeds[0]["data"]
    flipped_blob = session.feeds[1]["data"]
    assert first_blob[0, 0, 0, 0] > 0.99
    assert flipped_blob[0, 0, 0, -1] > 0.99
    assert embedding.shape == (512,)
    assert np.linalg.norm(embedding) == pytest.approx(1.0)


def test_adaface_embedding_can_disable_flip_fusion():
    engine = FaceEngine.__new__(FaceEngine)
    session = _FakeSession()
    engine._adaface_session = session
    engine._adaface_input_name = "data"
    engine._adaface_flip_fusion_enabled = False
    face_roi = np.zeros((112, 112, 3), dtype=np.uint8)

    engine._get_embedding_adaface(face_roi)

    assert len(session.feeds) == 1


def test_enrolled_embeddings_are_normalized_and_invalid_vectors_are_skipped():
    engine = FaceEngine.__new__(FaceEngine)

    valid = np.ones(512, dtype=np.float32)
    engine.load_enrolled_embeddings([
        valid.tolist(),
        np.zeros(512, dtype=np.float32).tolist(),
        [float("nan")] * 512,
    ])

    assert len(engine._enrolled_embeddings) == 1
    assert engine._enrolled_matrix.shape == (1, 512)
    assert np.linalg.norm(engine._enrolled_matrix[0]) == pytest.approx(1.0)


def test_enrolled_embeddings_reject_wrong_model_width_vectors():
    engine = FaceEngine.__new__(FaceEngine)

    engine.load_enrolled_embeddings([
        [1.0, 0.0, 0.0],
        np.ones(513, dtype=np.float32).tolist(),
    ])

    assert engine._enrolled_embeddings == []
    assert engine._enrolled_matrix is None


def test_identity_scoring_normalizes_live_embedding_before_cosine_match():
    engine = FaceEngine.__new__(FaceEngine)
    engine._enrolled_matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    engine._adaface_session = None
    face = SimpleNamespace(normed_embedding=np.array([10.0, 0.0], dtype=np.float32))
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    score, idx = engine._score_face_identity(frame, face)

    assert idx == 0
    assert score == pytest.approx(1.0)
