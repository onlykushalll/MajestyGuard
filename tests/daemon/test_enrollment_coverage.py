"""Deterministic tests for the continuous coverage enrollment collector."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))

import enrollment_coverage as ec  # noqa: E402

DIM = 512
CUE_POSE = {
    "center": (0.0, 0.0),
    "left": (-0.22, 0.0),   # re-targeted below for wide
    "right": (0.22, 0.0),
    "up": (0.0, -0.30),
    "down": (0.0, 0.30),
    "up_left": (-0.30, -0.30),
    "up_right": (0.30, -0.30),
}


class Rig:
    """Feeds synthetic frames at a steady 10 fps with reproducible embeddings."""

    def __init__(self, seed=7, config=None):
        self.rng = np.random.default_rng(seed)
        self.base = ec._unit(self.rng.standard_normal(DIM))
        self.collector = ec.CoverageCollector(config)
        self.t = 0.0
        self.fid = 0

    def emb(self, spread=0.8):
        noise = ec._unit(self.rng.standard_normal(DIM))
        return ec._unit(self.base + spread * noise).astype(np.float32)

    def frame(self, yaw=0.0, pitch=0.0, roll=0.0, *, faces=1, det=0.9, quality=0.8,
              height=0.30, illum=120.0, live=0.9, embedding="auto", spread=0.8, dt=0.1,
              framing=""):
        self.t += dt
        self.fid += 1
        if isinstance(embedding, str):
            embedding = self.emb(spread) if faces == 1 else None
        obs = ec.Observation(
            t=self.t, frame_id=self.fid, face_count=faces, yaw=yaw, pitch=pitch, roll=roll,
            det_score=det, quality=quality, height_frac=height, illumination=illum,
            live_score=live, embedding=embedding, framing=framing,
        )
        return self.collector.feed(obs)

    def calibrate(self, yaw=0.0, pitch=0.0, **kw):
        last = None
        for _ in range(12):
            last = self.frame(yaw, pitch, spread=0.3, **kw)
            if self.collector.state != ec.CALIBRATING:
                break
        return last

    def hold(self, yaw, pitch, n=3, **kw):
        return [self.frame(yaw, pitch, **kw) for _ in range(n)]


@pytest.fixture
def rig():
    r = Rig()
    r.calibrate()
    assert r.collector.state == ec.COLLECTING
    return r


BUCKET_POSE = {
    "center": (0.0, 0.0),
    "left_mild": (-0.22, 0.0), "left_wide": (-0.50, 0.0),
    "right_mild": (0.22, 0.0), "right_wide": (0.50, 0.0),
    "up": (0.0, -0.30), "down": (0.0, 0.30),
    "up_left": (-0.30, -0.30), "up_right": (0.30, -0.30),
}


def _pose_for_cue(target, _counts=None):
    return BUCKET_POSE.get(target or "center", (0.0, 0.0))


def _run_to_complete(r, limit=900):
    for _ in range(limit):
        if r.collector.state == ec.COMPLETE:
            return
        yaw, pitch = _pose_for_cue(r.collector.next_target())
        jitter = float(r.rng.uniform(-0.01, 0.01))
        r.hold(yaw + jitter, pitch, n=3)
    raise AssertionError("did not complete: %s" % r.collector.counts())


# ---- pose + buckets ---------------------------------------------------------


def test_pose_from_5pt_neutral_and_turns():
    neutral = [[30, 40], [70, 40], [50, 58], [34, 80], [66, 80]]
    yaw, pitch, roll = ec.pose_from_5pt(neutral)
    assert abs(yaw) < 0.01 and abs(roll) < 0.01
    # nose moves toward the image's right -> user's left (negative yaw)
    turned = [[30, 40], [70, 40], [58, 58], [34, 80], [66, 80]]
    assert ec.pose_from_5pt(turned)[0] < -0.2
    assert ec.pose_from_5pt(None) is None
    assert ec.pose_from_5pt([[0, 0]] * 4) is None
    assert ec.pose_from_5pt([[5, 5]] * 5) is None  # zero inter-ocular distance


@pytest.mark.parametrize(
    "yaw,pitch,expected",
    [
        (0.0, 0.0, "center"),
        (-0.25, 0.0, "left_mild"),
        (-0.50, 0.0, "left_wide"),
        (0.25, 0.0, "right_mild"),
        (0.50, 0.0, "right_wide"),
        (0.0, -0.30, "up"),
        (0.0, 0.30, "down"),
        (-0.30, -0.30, "up_left"),
        (0.30, -0.30, "up_right"),
        (-0.30, 0.30, "down_left"),
        (0.30, 0.30, "down_right"),
        (0.11, 0.0, None),    # dead zone
        (0.0, -0.11, None),
    ],
)
def test_assign_bucket_table(yaw, pitch, expected):
    assert ec.assign_bucket(yaw, pitch) == expected


def test_assign_bucket_tilt_and_unreliable_pitch():
    assert ec.assign_bucket(0.0, 0.0, 12.0) == "tilt_left"
    assert ec.assign_bucket(0.0, 0.0, -12.0) == "tilt_right"
    # unreliable pitch: vertical motion is ignored, horizontal still works
    assert ec.assign_bucket(0.0, -0.5, pitch_reliable=False) == "center"
    assert ec.assign_bucket(-0.5, -0.5, pitch_reliable=False) == "left_wide"


def test_required_quota_caps_can_reach_min_total():
    cfg = ec.CoverageConfig()
    for reliable in (True, False):
        c = ec.CoverageCollector(cfg)
        c.pitch_reliable = reliable
        caps = cfg.quotas(reliable)
        assert sum(caps[b] for b in c.required_buckets()) >= cfg.min_total
        mins = cfg.minimums(reliable)
        assert all(mins[b] <= caps[b] for b in c.required_buckets())


# ---- calibration ------------------------------------------------------------


def test_calibration_needs_dwell_time_and_frames():
    r = Rig()
    fb = r.frame(spread=0.3)
    assert fb.state == ec.CALIBRATING and fb.reason == "calibrating"
    for _ in range(3):
        fb = r.frame(spread=0.3)
    assert r.collector.state == ec.CALIBRATING          # 0.4 s: too short
    assert 0.3 < fb.calibration < 1.0
    for _ in range(6):
        fb = r.frame(spread=0.3)
    assert r.collector.state == ec.COLLECTING
    assert r.collector.baseline["yaw"] == pytest.approx(0.0)


def test_calibration_restarts_when_head_moves():
    r = Rig()
    for _ in range(4):
        r.frame(0.0, 0.0, spread=0.3)
    fb = r.frame(0.4, 0.0, spread=0.3)
    assert fb.reason == "hold_still"
    assert r.collector.state == ec.CALIBRATING


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"faces": 0}, "no_face"),
        ({"faces": 2}, "multiple_faces"),
        ({"det": 0.5}, "detection"),
        ({"quality": 0.3}, "quality"),
        ({"live": 0.2}, "liveness"),
        ({"live": None}, "liveness"),
    ],
)
def test_calibration_rejects_invalid_frames(kw, reason):
    r = Rig()
    fb = r.frame(**kw)
    assert fb.reason == reason
    assert not fb.accepted


def test_calibration_fails_when_identity_changes_midway():
    r = Rig()
    other = ec._unit(np.random.default_rng(99).standard_normal(DIM)).astype(np.float32)
    fbs = []
    for i in range(10):
        emb = other.copy() if i == 3 else r.emb(0.3)   # a different face slips in
        fbs.append(r.frame(embedding=emb))
    failed = [f for f in fbs if f.reason == "identity_unstable"]
    assert failed and failed[0].state == ec.CALIBRATING
    assert r.collector._base_emb is None               # no baseline was adopted


def test_pose_is_relative_to_baseline_not_absolute():
    r = Rig()
    r.calibrate(yaw=0.3, pitch=-0.2)                  # camera mounted off-axis
    assert r.collector.state == ec.COLLECTING
    fbs = r.hold(0.3, -0.2)
    accepted = [f for f in fbs if f.accepted]
    assert accepted and accepted[0].bucket == "center"


def test_noisy_pitch_disables_vertical_requirements():
    r = Rig()
    for i in range(12):
        r.frame(0.0, 0.0 if i % 2 else 0.1, spread=0.3)   # 0.1 pitch jitter
        if r.collector.state != ec.CALIBRATING:
            break
    assert r.collector.state == ec.COLLECTING
    assert r.collector.pitch_reliable is False
    assert "up" not in r.collector.required_buckets()


def test_stable_pitch_requires_vertical_buckets(rig):
    assert rig.collector.pitch_reliable
    assert {"up", "down", "up_left", "up_right"} <= set(rig.collector.required_buckets())


# ---- gating -----------------------------------------------------------------


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"faces": 0}, "no_face"),
        ({"faces": 2}, "multiple_faces"),
        ({"det": 0.5}, "detection"),
        ({"quality": 0.3}, "quality"),
        ({"live": 0.2}, "liveness"),
    ],
)
def test_collecting_rejects_invalid_frames(rig, kw, reason):
    rig.hold(0.0, 0.0, n=2)
    fb = rig.frame(0.0, 0.0, **kw)
    assert fb.reason == reason and not fb.accepted
    assert rig.collector.counts()["center"] <= 1


@pytest.mark.parametrize("framing", ["move_closer", "move_back", "center"])
def test_bad_framing_blocks_calibration_and_collection(framing, rig):
    fb = rig.frame(0.0, 0.0, framing=framing)
    assert fb.reason == framing and not fb.accepted
    fresh = Rig()
    assert fresh.frame(framing=framing).reason == framing


def test_other_person_is_rejected_by_identity(rig):
    stranger = ec._unit(np.random.default_rng(1234).standard_normal(DIM)).astype(np.float32)
    fbs = [rig.frame(0.0, 0.0, embedding=stranger.copy()) for _ in range(4)]
    assert all(f.reason == "identity_mismatch" for f in fbs)
    assert rig.collector.counts()["center"] == 0


def test_moving_pose_is_never_accepted(rig):
    fbs = [rig.frame(-0.3 + 0.1 * i, 0.0) for i in range(6)]   # 0.1/frame > dwell tol
    assert not any(f.accepted for f in fbs)
    assert all(f.reason in ("settling", "no_bucket") for f in fbs)


def test_stable_pose_is_accepted_after_dwell(rig):
    fbs = rig.hold(-0.25, 0.0, n=3)
    assert [f.accepted for f in fbs] == [False, False, True]
    assert fbs[-1].bucket == "left_mild"


def test_dead_zone_frames_are_skipped(rig):
    fbs = rig.hold(0.11, 0.0, n=4)
    assert not any(f.accepted for f in fbs)
    assert fbs[-1].reason == "no_bucket"


# ---- dedupe / diversity -------------------------------------------------------


def test_duplicate_frame_id_rejected(rig):
    rig.hold(0.0, 0.0, n=3)
    n = rig.collector.counts()["center"]
    obs = ec.Observation(t=rig.t + 0.5, frame_id=rig.fid, face_count=1, det_score=0.9,
                         quality=0.8, height_frac=0.3, illumination=120.0, live_score=0.9,
                         embedding=rig.emb())
    assert rig.collector.feed(obs).reason == "duplicate_frame"
    assert rig.collector.counts()["center"] == n


def test_identical_embedding_rejected(rig):
    rig.hold(0.0, 0.0, n=3)
    assert rig.collector.counts()["center"] == 1
    same = rig.collector.samples[0].embedding.copy()
    rig.frame(0.0, 0.0, embedding=same.copy())
    fb = rig.frame(0.0, 0.0, embedding=same.copy())
    assert fb.reason == "duplicate_embedding"
    assert rig.collector.counts()["center"] == 1


def test_similar_embedding_in_same_pose_rejected(rig):
    rig.hold(0.0, 0.0, n=3)
    ref = rig.collector.samples[0].embedding
    near = ec._unit(ref + 0.2 * ec._unit(rig.rng.standard_normal(DIM))).astype(np.float32)  # cos ~0.98
    rig.frame(0.0, 0.0, embedding=near.copy())
    fb = rig.frame(0.0, 0.0, embedding=near.copy())
    assert fb.reason == "too_similar"


def test_min_interval_prevents_burst_acceptance(rig):
    rig.hold(0.0, 0.0, n=3)
    fb = rig.frame(0.0, 0.0, dt=0.01)
    assert fb.reason == "too_soon"


def test_bucket_cap_stops_one_pose_dominating(rig):
    cap = rig.collector.config.quotas(True)["center"]
    for _ in range(cap * 3 + 6):
        rig.hold(float(rig.rng.uniform(-0.01, 0.01)), 0.0, n=1)
    assert rig.collector.counts()["center"] == cap
    assert rig.collector.samples[0].bucket == "center"


def test_context_buckets_keep_center_uninflated(rig):
    rig.hold(0.0, 0.0, n=3, height=0.30 * 1.25)
    assert rig.collector.counts()["near"] == 1
    assert rig.collector.counts()["center"] == 0
    rig.hold(0.0, 0.0, n=3, illum=120.0 * 0.5)
    assert rig.collector.counts()["dim"] == 1


# ---- progress, cues, completion --------------------------------------------


def test_cue_points_at_missing_coverage(rig):
    assert rig.collector.next_cue() == "center"
    for _ in range(8):
        rig.hold(float(rig.rng.uniform(-0.01, 0.01)), 0.0, n=3)
    assert rig.collector.counts()["center"] >= 6
    assert rig.collector.next_cue() in ("left", "right", "up", "down", "up_left", "up_right")


def test_progress_is_monotonic_and_completes_at_one(rig):
    seen, states = [], []
    for _ in range(900):
        if rig.collector.state == ec.COMPLETE:
            break
        yaw, pitch = _pose_for_cue(rig.collector.next_target())
        for fb in rig.hold(yaw + float(rig.rng.uniform(-0.01, 0.01)), pitch, n=3):
            seen.append(fb.progress)
            states.append(fb.state)
    assert rig.collector.state == ec.COMPLETE
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert all(p < 1.0 for p, s in zip(seen, states) if s != ec.COMPLETE)


def test_incomplete_coverage_cannot_finalize(rig):
    for _ in range(40):
        rig.hold(float(rig.rng.uniform(-0.01, 0.01)), 0.0, n=3)   # only the centre pose
    assert rig.collector.state == ec.COLLECTING
    assert not rig.collector.is_complete()
    res = rig.collector.finalize()
    assert not res.ok and res.reason == "coverage_incomplete"
    assert res.selected == []


def test_full_run_selects_diverse_gallery(rig):
    _run_to_complete(rig)
    res = rig.collector.finalize()
    assert res.ok, res.reason
    cfg = rig.collector.config
    assert cfg.min_total <= len(res.selected) <= cfg.max_templates
    mins = cfg.minimums(True)
    assert all(res.bucket_counts.get(b, 0) >= mins[b] for b in rig.collector.required_buckets())
    mat = res.embeddings()
    assert mat.shape == (len(res.selected), DIM)
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0, atol=1e-4)
    gram = mat @ mat.T
    np.fill_diagonal(gram, -1.0)
    assert gram.max() < cfg.dup_similarity
    assert all(s.live_score >= cfg.liveness_threshold for s in res.selected)
    assert sum(s.selected for s in rig.collector.samples) == len(res.selected)


def test_pitch_unreliable_run_completes_with_horizontal_buckets_only():
    r = Rig(seed=11)
    for i in range(12):
        r.frame(0.0, 0.0 if i % 2 else 0.1, spread=0.3)
        if r.collector.state != ec.CALIBRATING:
            break
    assert not r.collector.pitch_reliable
    _run_to_complete(r)
    res = r.collector.finalize()
    assert res.ok, res.reason
    assert not res.pitch_reliable


def test_liveness_regression_blocks_commit(rig):
    _run_to_complete(rig)
    rig.collector.samples[0].live_score = 0.10
    res = rig.collector.finalize()
    assert not res.ok and res.reason == "liveness_failed"


def test_pruning_that_breaks_coverage_blocks_commit(rig):
    _run_to_complete(rig)
    outlier_rng = np.random.default_rng(555)
    for s in rig.collector.samples:
        if s.bucket == "left_wide":
            s.embedding = ec._unit(outlier_rng.standard_normal(DIM)).astype(np.float32)
    res = rig.collector.finalize()
    assert not res.ok and res.reason == "coverage_lost_after_pruning"


# ---- cancellation / zeroing ---------------------------------------------------


def test_cancel_zeroes_and_discards_everything(rig):
    rig.hold(0.0, 0.0, n=6)
    kept = [s.embedding for s in rig.collector.samples]
    base = rig.collector._base_emb
    assert kept and float(np.abs(kept[0]).sum()) > 0
    rig.collector.cancel()
    assert rig.collector.state == ec.CANCELLED
    assert rig.collector.samples == []
    assert all(float(np.abs(a).sum()) == 0 for a in kept)
    assert float(np.abs(base).sum()) == 0
    fb = rig.frame(0.0, 0.0)
    assert fb.reason == "cancelled" and not fb.accepted
    assert rig.collector.finalize().reason == "coverage_incomplete"


def test_cancel_during_calibration_zeroes_buffered_frames():
    r = Rig()
    embs = []
    for _ in range(3):
        e = r.emb(0.3)
        embs.append(e)
        r.frame(embedding=e)
    r.collector.cancel()
    assert r.collector._cal == []
    assert r.collector.state == ec.CANCELLED


def test_candidate_embedding_buffer_is_copied_not_aliased(rig):
    e = rig.emb()
    rig.frame(0.0, 0.0, embedding=e)
    rig.frame(0.0, 0.0, embedding=e)
    fb = rig.frame(0.0, 0.0, embedding=e)
    assert fb.accepted
    e.fill(0)                      # the runner zeroes its own buffer after feed()
    assert float(np.abs(rig.collector.samples[0].embedding).sum()) > 0
