"""Runner-level tests for continuous enrollment (no camera, no models)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))

import enroll_continuous as run  # noqa: E402
import enrollment_commit as ecm  # noqa: E402
import enrollment_coverage as ec  # noqa: E402

DIM = 512
NEUTRAL_KPS = np.array([[30, 40], [70, 40], [50, 58], [34, 80], [66, 80]], dtype=np.float32)


def fake_protect(data, description):
    return b"ENC:" + data


def fake_is_protected(data):
    return data.startswith(b"ENC:")


def good_face(emb=None, bbox=(200, 120, 440, 400), kps=NEUTRAL_KPS, det=0.9):
    if emb is None:
        emb = ec._unit(np.random.default_rng(1).standard_normal(DIM)).astype(np.float32)
    return SimpleNamespace(bbox=np.array(bbox, dtype=np.float32), kps=kps.copy(), det_score=det,
                           normed_embedding=emb)


def make_analyzer(faces, *, quality=0.8, height=0.30, live=0.9, enhance=None):
    calls = {"live": 0}

    def liveness(frame, face):
        calls["live"] += 1
        return live

    an = run.Analyzer(
        detect=lambda frame: faces,
        measure_quality=lambda frame, face: SimpleNamespace(score=quality, height_frac=height,
                                                            illumination_mean=120.0),
        liveness_score=liveness,
        enhance=enhance or (lambda f: f),
        config=ec.CoverageConfig(),
    )
    return an, calls


FRAME = lambda: np.full((480, 640, 3), 90, dtype=np.uint8)  # noqa: E731


# ---- framing ------------------------------------------------------------------


@pytest.mark.parametrize(
    "h,dx,dy,expected",
    [(0.30, 0.0, 0.0, ""), (0.20, 0.0, 0.0, "move_closer"), (0.80, 0.0, 0.0, "move_back"),
     (0.30, 0.5, 0.0, "center"), (0.30, 0.0, 0.5, "center")],
)
def test_framing_problem(h, dx, dy, expected):
    assert run.framing_problem(h, dx, dy) == expected


# ---- Analyzer ---------------------------------------------------------------


def test_no_face_and_multiple_faces_skip_liveness():
    for faces, count in (([], 0), ([good_face(), good_face()], 2)):
        an, calls = make_analyzer(faces)
        obs = an.observe(FRAME(), 1.0, 1)
        assert obs.face_count == count and obs.embedding is None
        assert calls["live"] == 0


def test_good_face_produces_full_observation_with_copied_embedding():
    face = good_face()
    original = face.normed_embedding.copy()
    an, calls = make_analyzer([face])
    obs = an.observe(FRAME(), 2.0, 7)
    assert obs.face_count == 1 and obs.frame_id == 7 and obs.live_score == 0.9
    assert obs.framing == "" and calls["live"] == 1
    assert abs(obs.yaw) < 0.01
    np.testing.assert_allclose(obs.embedding, original)
    # the detector's own buffer was zeroed, our copy survives
    assert float(np.abs(face.normed_embedding).sum()) == 0.0
    assert float(np.abs(obs.embedding).sum()) > 0.0
    ec._zero(obs.embedding)


def test_bad_framing_or_quality_skips_liveness():
    an, calls = make_analyzer([good_face()], height=0.15)
    assert an.observe(FRAME(), 1.0, 1).framing == "move_closer"
    an, calls2 = make_analyzer([good_face()], quality=0.2)
    obs = an.observe(FRAME(), 1.0, 1)
    assert obs.live_score is None
    assert calls["live"] == 0 and calls2["live"] == 0


def test_missing_landmarks_means_no_embedding_and_no_liveness():
    face = good_face()
    face.kps = None
    an, calls = make_analyzer([face])
    obs = an.observe(FRAME(), 1.0, 1)
    assert obs.embedding is None and calls["live"] == 0


def test_enhanced_copy_is_zeroed():
    made = []

    def enhance(f):
        c = f.copy()
        made.append(c)
        return c

    an, _ = make_analyzer([good_face()], enhance=enhance)
    obs = an.observe(FRAME(), 1.0, 1)
    assert int(made[0].sum()) == 0
    ec._zero(obs.embedding)


# ---- session loop ---------------------------------------------------------------


class FakeCap:
    def __init__(self, ok=True, frames=None):
        self.ok = ok
        self.issued = []
        self.reads = 0

    def read(self):
        self.reads += 1
        if not self.ok:
            return False, None
        f = np.full((48, 64, 3), 77, dtype=np.uint8)
        self.issued.append(f)
        return True, f


class ScriptedAnalyzer:
    """Steers a synthetic user toward whatever bucket the collector asks for."""

    POSE = {
        "center": (0.0, 0.0), "left_mild": (-0.22, 0.0), "left_wide": (-0.5, 0.0),
        "right_mild": (0.22, 0.0), "right_wide": (0.5, 0.0), "up": (0.0, -0.3),
        "down": (0.0, 0.3), "up_left": (-0.3, -0.3), "up_right": (0.3, -0.3),
    }

    def __init__(self, collector, seed=5):
        self.collector = collector
        self.rng = np.random.default_rng(seed)
        self.base = ec._unit(self.rng.standard_normal(DIM))
        self.n = 0
        self.pose = (0.0, 0.0)

    def observe(self, raw, t, frame_id):
        self.n += 1
        target = self.collector.next_target()
        if self.n % 3 == 1 and target:
            yaw, pitch = self.POSE.get(target, (0.0, 0.0))
            self.pose = (yaw + float(self.rng.uniform(-0.01, 0.01)), pitch)
        spread = 0.3 if self.collector.state == ec.CALIBRATING else 0.8
        emb = ec._unit(self.base + spread * ec._unit(self.rng.standard_normal(DIM))).astype(np.float32)
        return ec.Observation(t=t, frame_id=frame_id, face_count=1, yaw=self.pose[0], pitch=self.pose[1],
                              det_score=0.9, quality=0.8, height_frac=0.3, illumination=120.0,
                              live_score=0.9, embedding=emb)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.1)      # one camera frame ~ 100 ms of simulated time


def session(cap, analyzer, collector, **kw):
    clock = kw.pop("clock", Clock())
    previews, feedbacks = [], []
    outcome = run.run_session(
        cap, analyzer, collector,
        emit_preview=lambda a: previews.append(a.copy()), emit_feedback=feedbacks.append,
        should_stop=kw.pop("should_stop", lambda: False), now=clock, sleep=clock.sleep,
        make_preview=kw.pop("make_preview", None), **kw,
    )
    return outcome, previews, feedbacks


def test_session_completes_and_finish_commits(tmp_path):
    collector = ec.CoverageCollector()
    cap = FakeCap()
    outcome, _, feedbacks = session(cap, ScriptedAnalyzer(collector), collector)
    assert outcome.reason == "complete" and collector.state == ec.COMPLETE
    assert feedbacks[-1].progress == 1.0
    assert all(int(f.sum()) == 0 for f in cap.issued)         # every raw frame zeroed

    gallery, meta = tmp_path / "g.npy", tmp_path / "m.json"
    res = run.finish(
        collector, dry_run=False, gallery_path=gallery, meta_path=meta,
        commit=lambda r, c, **kw: ecm.commit_result(r, c, protect=fake_protect,
                                                    is_protected=fake_is_protected, **kw),
    )
    assert res.ok and 50 <= res.selected <= 60
    assert gallery.exists() and meta.exists()
    assert collector.samples == []                           # discarded after commit


def test_dry_run_validates_but_writes_nothing(tmp_path):
    collector = ec.CoverageCollector()
    session(FakeCap(), ScriptedAnalyzer(collector), collector)

    def boom(*a, **k):
        raise AssertionError("must not commit on a dry run")

    res = run.finish(collector, dry_run=True, gallery_path=tmp_path / "g", meta_path=tmp_path / "m",
                     commit=boom)
    assert res.ok and "nothing was saved" in res.message.lower()
    assert not (tmp_path / "g").exists()


def test_incomplete_coverage_never_reaches_commit(tmp_path):
    collector = ec.CoverageCollector()          # still calibrating, nothing collected

    def boom(*a, **k):
        raise AssertionError("commit must not run")

    res = run.finish(collector, dry_run=False, gallery_path=tmp_path / "g", meta_path=tmp_path / "m",
                     commit=boom)
    assert not res.ok and "Nothing was saved" in res.message
    assert not (tmp_path / "g").exists()


def test_commit_failure_is_reported_honestly(tmp_path):
    collector = ec.CoverageCollector()
    session(FakeCap(), ScriptedAnalyzer(collector), collector)
    bad = lambda r, c, **kw: ecm.CommitOutcome(False, "write_failed:disk", rolled_back=False)  # noqa: E731
    res = run.finish(collector, dry_run=False, gallery_path=tmp_path / "g", meta_path=tmp_path / "m",
                     commit=bad)
    assert not res.ok and "write_failed:disk" in res.message and "Restoring" in res.message


def test_cancel_stops_and_zeroes_without_writing():
    collector = ec.CoverageCollector()
    analyzer = ScriptedAnalyzer(collector)
    cap = FakeCap()
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 20

    outcome, _, _ = session(cap, analyzer, collector, should_stop=should_stop)
    assert outcome.reason == "cancelled"
    assert collector.state == ec.CANCELLED and collector.samples == []
    assert collector.finalize().ok is False


def test_deadline_aborts():
    collector = ec.CoverageCollector()
    clock = Clock()
    outcome, _, _ = session(FakeCap(), ScriptedAnalyzer(collector), collector, clock=clock, deadline=2.0)
    assert outcome.reason == "deadline" and collector.state == ec.CANCELLED


def test_camera_failure_backs_off_and_gives_up():
    collector = ec.CoverageCollector()
    clock = Clock()
    sleeps = []
    cap = FakeCap(ok=False)
    outcome = run.run_session(
        cap, ScriptedAnalyzer(collector), collector,
        emit_preview=lambda a: None, emit_feedback=lambda f: None, should_stop=lambda: False,
        now=clock, sleep=lambda s: sleeps.append(s),
    )
    assert outcome.reason == "camera"
    assert cap.reads == run.MAX_CAMERA_FAILURES
    assert len(sleeps) == run.MAX_CAMERA_FAILURES - 1 and min(sleeps) >= 0.05   # no busy loop


def test_camera_recovers_after_transient_failures():
    collector = ec.CoverageCollector()

    class Flaky(FakeCap):
        def read(self):
            self.reads += 1
            if self.reads % 7 == 0:
                return False, None
            return super().read()

    cap = Flaky()
    outcome, _, _ = session(cap, ScriptedAnalyzer(collector), collector)
    assert outcome.reason == "complete"


def test_preview_is_emitted_before_feedback_and_made_by_callback():
    collector = ec.CoverageCollector()
    order = []
    clock = Clock()
    run.run_session(
        FakeCap(), ScriptedAnalyzer(collector), collector,
        emit_preview=lambda a: order.append("p"), emit_feedback=lambda f: order.append("f"),
        should_stop=lambda: len(order) >= 6, now=clock, sleep=clock.sleep,
        make_preview=lambda raw: raw.copy(),
    )
    assert order[:4] == ["p", "f", "p", "f"]


def test_session_zeroes_observation_embeddings():
    collector = ec.CoverageCollector()
    made = []

    class Spy(ScriptedAnalyzer):
        def observe(self, raw, t, frame_id):
            obs = super().observe(raw, t, frame_id)
            made.append(obs.embedding)
            return obs

    session(FakeCap(), Spy(collector), collector, should_stop=lambda: len(made) >= 15)
    assert made and all(float(np.abs(e).sum()) == 0.0 for e in made)
