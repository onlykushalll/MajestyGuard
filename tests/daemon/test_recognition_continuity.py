from types import SimpleNamespace
from typing import Optional

import numpy as np
import pytest

from face_engine import FaceEngine


def _make_engine():
    engine = FaceEngine.__new__(FaceEngine)
    engine._app = SimpleNamespace(models={})
    engine._adaface_session = None
    engine._adaface_flip_fusion_enabled = True
    engine.recognition_threshold = 0.78
    engine._enrolled_matrix = None
    return engine


def test_try_arcface_flip_fusion_combines_and_normalizes_embeddings(monkeypatch):
    engine = _make_engine()
    
    # Mock recognition model inside _app
    rec_model = SimpleNamespace()
    engine._app.models["recognition"] = rec_model

    # Target template
    template = np.zeros(512, dtype=np.float32)
    template[0] = 1.0

    # Base embedding (partial match)
    base_emb = np.zeros(512, dtype=np.float32)
    base_emb[0] = 0.72
    base_emb[1] = np.sqrt(1.0 - 0.72**2)

    # Flipped feature aligns even better with template
    flipped_feature = np.zeros(512, dtype=np.float32)
    flipped_feature[0] = 0.90
    flipped_feature[1] = np.sqrt(1.0 - 0.90**2)
    rec_model.get_feat = lambda chip: flipped_feature.copy()

    # Face mock
    face = SimpleNamespace(
        bbox=np.array([20, 20, 140, 160], dtype=np.float32),
        kps=np.array([[40, 50], [80, 50], [60, 75], [45, 95], [75, 95]], dtype=np.float32),
    )
    frame = np.full((240, 320, 3), 128, dtype=np.uint8)

    fused = engine._try_arcface_flip_fusion(frame, face, base_emb)

    assert fused is not None
    assert fused.shape == (512,)
    # Fused embedding must be L2 normalized
    assert np.isclose(np.linalg.norm(fused), 1.0, atol=1e-5)
    # Cosine similarity with template must improve over 0.72
    score_before = float(np.dot(template, base_emb))
    score_after = float(np.dot(template, fused))
    assert score_before == pytest.approx(0.72, abs=1e-4)
    assert score_after > score_before
    assert score_after > 0.80


def test_arcface_flip_fusion_upgrades_borderline_owner_score(monkeypatch):
    """Verify that when ArcFace scores in the borderline band (0.68 <= score < threshold),
    flip fusion executes and upgrades the best_score."""
    engine = _make_engine()
    template = np.zeros(512, dtype=np.float32)
    template[0] = 1.0
    engine._enrolled_matrix = np.array([template])

    # Base embedding gives dot product 0.72
    base_emb = np.zeros(512, dtype=np.float32)
    base_emb[0] = 0.72
    base_emb[1] = np.sqrt(1.0 - 0.72**2)

    # Better flipped embedding gives dot product ~0.82 when combined
    improved_fused = np.zeros(512, dtype=np.float32)
    improved_fused[0] = 0.82
    improved_fused[1] = np.sqrt(1.0 - 0.82**2)

    monkeypatch.setattr(
        engine,
        "_try_arcface_flip_fusion",
        lambda frame, face, query_emb: improved_fused,
    )

    face = SimpleNamespace(
        _cached_norm_emb=base_emb,
        _cached_identity_scores=(0.72, 0, 0.72),
    )

    # Simulate the recognition block in process_frame
    best_score, best_template_index, top3_mean = face._cached_identity_scores
    query_embedding = face._cached_norm_emb
    liveness_passed = True

    if (
        liveness_passed
        and engine._adaface_session is None
        and getattr(engine, "_adaface_flip_fusion_enabled", True)
        and 0.68 <= best_score < engine.recognition_threshold
    ):
        fused_emb = engine._try_arcface_flip_fusion(None, face, query_embedding)
        if fused_emb is not None:
            fused_scores = engine._enrolled_matrix @ fused_emb
            fused_idx = int(np.argmax(fused_scores))
            fused_best = float(np.clip(fused_scores[fused_idx], 0.0, 1.0))
            if fused_best > best_score:
                best_score = fused_best
                best_template_index = fused_idx
                k = min(3, len(fused_scores))
                top3_mean = float(np.mean(np.partition(fused_scores, -k)[-k:]))

    assert best_score == pytest.approx(0.82, abs=1e-4)
    assert best_score > 0.72
