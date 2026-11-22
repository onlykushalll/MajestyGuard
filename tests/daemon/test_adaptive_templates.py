import numpy as np
import pytest

import adaptive_templates as A

DIM = 512


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _near(base, sim, rng):
    """Unit vector with cosine ``sim`` to ``base``."""
    noise = rng.standard_normal(base.shape).astype(np.float32)
    noise -= (noise @ base) * base
    noise = _unit(noise)
    return _unit(sim * base + np.sqrt(1 - sim * sim) * noise)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig():
    rng = np.random.default_rng(7)
    base = _unit(rng.standard_normal(DIM).astype(np.float32))
    anchors = np.stack([_near(base, 0.97, rng) for _ in range(5)])
    clock = Clock()
    store = A.AdaptiveTemplateStore(None, anchors, clock=clock)
    return store, base, rng, clock


def _offer(store, emb, n=A.STEADY_FRAMES, **over):
    kw = dict(anchor_score=0.86, liveness=0.92, quality=0.8, face_count=1, owner_confirmed=True)
    kw.update(over)
    out = False
    for _ in range(n):
        out = store.consider(emb, **kw)
    return out


def test_a_clear_new_look_is_learned_after_steady_frames(rig):
    store, base, rng, _ = rig
    emb = _near(base, 0.84, rng)
    assert _offer(store, emb, n=A.STEADY_FRAMES - 1) is False
    assert store.consider(emb, anchor_score=0.86, liveness=0.92, quality=0.8, face_count=1, owner_confirmed=True)
    assert store.size == 1


@pytest.mark.parametrize("over", [
    dict(owner_confirmed=False), dict(face_count=2), dict(liveness=0.80), dict(quality=0.4),
    dict(anchor_score=0.78), dict(full_liveness=False),
])
def test_weak_or_ambiguous_frames_are_never_learned(rig, over):
    store, base, rng, _ = rig
    assert _offer(store, _near(base, 0.84, rng), n=6, **over) is False
    assert store.size == 0


def test_near_duplicates_add_nothing(rig):
    store, base, rng, _ = rig
    assert _offer(store, _near(base, 0.96, rng)) is False


def test_a_look_far_from_the_anchors_is_refused_even_with_a_high_claimed_score(rig):
    store, base, rng, _ = rig
    stranger = _near(base, 0.55, rng)
    assert _offer(store, stranger, anchor_score=0.9) is False


def test_pacing_gap_daily_limit_and_pool_cap(rig):
    store, base, rng, clock = rig
    assert _offer(store, _near(base, 0.84, rng))
    assert _offer(store, _near(base, 0.83, rng)) is False          # too soon
    for i in range(2):
        clock.t += A.MIN_GAP_S + 1
        assert _offer(store, _near(base, 0.82 + i * 0.01, rng))
    clock.t += A.MIN_GAP_S + 1
    assert _offer(store, _near(base, 0.85, rng)) is False          # 3 per day reached
    for _ in range(20):
        clock.t += 86400.0 + 1
        _offer(store, _near(base, 0.84, rng))
    assert store.size <= A.MAX_POOL


def test_the_boost_is_bounded_and_penalised(rig):
    store, base, rng, _ = rig
    look = _near(base, 0.84, rng)
    assert _offer(store, look)
    assert store.boosted(look, 0.70) == pytest.approx(0.78)         # capped at anchor + 0.08
    assert store.boosted(look, 0.99) == pytest.approx(0.99)         # anchors still win
    unrelated = _near(base, 0.2, rng)
    assert store.boosted(unrelated, 0.2) == pytest.approx(0.2, abs=0.05)


def test_disabled_store_is_inert(rig):
    _, base, rng, clock = rig
    off = A.AdaptiveTemplateStore(None, np.stack([base]), clock=clock, enabled=False)
    assert _offer(off, _near(base, 0.84, rng)) is False and off.boosted(base, 0.5) == 0.5


def test_pool_survives_restart_but_not_re_enrollment(tmp_path, rig):
    store, base, rng, clock = rig
    anchors = store._anchors
    ident = lambda b: b            # noqa: E731  (stand-in for DPAPI in tests)
    s1 = A.AdaptiveTemplateStore(tmp_path / "a.bin", anchors, protect=ident, unprotect=ident, clock=clock)
    assert _offer(s1, _near(base, 0.84, rng))
    s2 = A.AdaptiveTemplateStore(tmp_path / "a.bin", anchors, protect=ident, unprotect=ident, clock=clock)
    assert s2.size == 1
    other = np.stack([_unit(rng.standard_normal(DIM)) for _ in range(3)])
    s3 = A.AdaptiveTemplateStore(tmp_path / "a.bin", other, protect=ident, unprotect=ident, clock=clock)
    assert s3.size == 0 and not (tmp_path / "a.bin").exists()


def test_tampered_file_cannot_smuggle_in_a_stranger(tmp_path, rig):
    import io
    store, base, rng, clock = rig
    anchors = store._anchors
    ident = lambda b: b            # noqa: E731
    stranger = _near(base, 0.1, rng)
    buf = io.BytesIO()
    np.savez(buf, pool=np.stack([stranger]), added=np.asarray([clock.t - 5000]),
             fp=np.frombuffer(A.fingerprint(anchors).encode(), dtype=np.uint8))
    (tmp_path / "a.bin").write_bytes(buf.getvalue())
    s = A.AdaptiveTemplateStore(tmp_path / "a.bin", anchors, protect=ident, unprotect=ident, clock=clock)
    assert s.size == 0


def test_garbage_file_is_ignored(tmp_path, rig):
    store, *_ = rig
    (tmp_path / "a.bin").write_bytes(b"not an npz")
    ident = lambda b: b            # noqa: E731
    s = A.AdaptiveTemplateStore(tmp_path / "a.bin", store._anchors, protect=ident, unprotect=ident)
    assert s.size == 0


def test_clear_wipes_memory_and_disk(tmp_path, rig):
    store, base, rng, clock = rig
    ident = lambda b: b            # noqa: E731
    s = A.AdaptiveTemplateStore(tmp_path / "a.bin", store._anchors, protect=ident, unprotect=ident, clock=clock)
    assert _offer(s, _near(base, 0.84, rng))
    s.clear()
    assert s.size == 0 and not (tmp_path / "a.bin").exists()
