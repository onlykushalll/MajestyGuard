"""FaceEngine uses the adaptive pool only through the bounded boost, and only teaches it from strong anchor matches."""
import numpy as np

import adaptive_templates as A
from face_engine import FaceEngine


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _engine(anchors):
    eng = FaceEngine.__new__(FaceEngine)
    eng._enrolled_matrix = np.stack(anchors)
    eng._adaptive_store = None
    return eng


def test_without_a_store_scoring_is_plain_anchor_cosine():
    rng = np.random.default_rng(1)
    a = _unit(rng.standard_normal(512))
    eng = _engine([a])
    score, idx, top3, anchor = eng._score_vs_enrolled(a)
    assert score == anchor and idx == 0 and abs(score - 1.0) < 1e-5


def test_store_can_lift_but_never_beyond_the_cap():
    rng = np.random.default_rng(2)
    base = _unit(rng.standard_normal(512))
    noise = _unit(rng.standard_normal(512) - base * 0)
    look = _unit(0.7 * base + 0.714 * noise)               # ~0.7 from the anchor
    eng = _engine([base])
    store = A.AdaptiveTemplateStore(None, np.stack([base]))
    store._pool.append(look)
    store._added_at.append(0.0)
    eng._adaptive_store = store
    score, _, _, anchor = eng._score_vs_enrolled(look)
    assert anchor < 0.8
    assert anchor < score <= anchor + A.MAX_BOOST + 1e-6


def test_store_errors_never_break_scoring():
    rng = np.random.default_rng(3)
    base = _unit(rng.standard_normal(512))

    class Boom:
        def boosted(self, *a):
            raise RuntimeError("x")

    eng = _engine([base])
    eng._adaptive_store = Boom()
    assert eng._score_vs_enrolled(base)[0] > 0.99
