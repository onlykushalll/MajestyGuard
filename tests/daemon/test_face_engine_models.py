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

    assert blob[0, 2, 0, 0] > 0.99
    assert blob[0, 0, 0, 0] < -0.99


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
    assert first_blob[0, 2, 0, 0] > 0.99
    assert flipped_blob[0, 2, 0, -1] > 0.99
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

def test_verify_model_tofu_validates_against_shipped_reference_hashes(tmp_path, monkeypatch):
    from face_engine import _verify_model_tofu, _TOFU_VERIFIED
    import json

    _TOFU_VERIFIED.clear()
    manifest_file = tmp_path / "model_hashes.json"
    monkeypatch.setattr(face_engine, "_model_hashes_manifest_path", lambda: str(manifest_file))

    fake_model = tmp_path / "antispoof_minifasv2.onnx"
    fake_model.write_bytes(b"corrupt-or-tampered-model-content")

    assert _verify_model_tofu(str(fake_model)) is False
    assert not manifest_file.exists()

    unlisted_model = tmp_path / "custom_unlisted_model.onnx"
    unlisted_model.write_bytes(b"safe-custom-content")
    assert _verify_model_tofu(str(unlisted_model)) is True
    assert manifest_file.exists()
    saved_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert any("custom_unlisted_model.onnx" in k for k in saved_manifest)


def test_face_engine_single_face_quality_and_embedding_caching():
    from types import SimpleNamespace
    engine = FaceEngine.__new__(FaceEngine)
    engine._min_frame_quality = 0.5
    
    frame = np.full((120, 160, 3), 40, dtype=np.uint8)
    face = SimpleNamespace(
        bbox=np.array([20, 20, 100, 100], dtype=np.float32),
        det_score=0.95,
        normed_embedding=np.ones(512, dtype=np.float32),
        embedding=np.ones(512, dtype=np.float32),
    )
    
    # Measure quality
    q1 = engine._frame_quality(frame, face)
    assert hasattr(face, "_cached_face_quality")
    assert face._cached_face_quality is not None
    # Check cached quality is reused
    q2 = engine._frame_quality(frame, face)
    assert q1 == q2
    
    # Zeroing face data clears cached attributes
    engine._zero_face_data([face])
    assert face._cached_norm_emb is None
    assert face._cached_face_quality is None


def test_face_engine_low_light_chroma_denoising():
    engine = FaceEngine.__new__(FaceEngine)
    import cv2
    engine._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    
    # Dark frame (< 100 mean L)
    dark_frame = np.full((100, 100, 3), 30, dtype=np.uint8)
    enhanced = engine._enhance_frame(dark_frame)
    assert enhanced.shape == dark_frame.shape
    assert enhanced.dtype == np.uint8
    # Well-lit frame (> 100 mean L) returns unchanged
    bright_frame = np.full((100, 100, 3), 200, dtype=np.uint8)
    enhanced_bright = engine._enhance_frame(bright_frame)
    assert np.array_equal(enhanced_bright, bright_frame)
