"""Continuous, hands-free coverage enrollment: pure decision logic.

No camera, no model, no Qt and no clock live here. A runner feeds one
``Observation`` per camera frame into ``CoverageCollector`` and gets back a
``Feedback`` describing what the user should see (progress, next direction,
why a frame was skipped). All thresholds are in ``CoverageConfig`` so tests are
deterministic.

Flow
----
1. CALIBRATING  ~0.75 s of still, valid frames fix a neutral baseline (pose,
   face size, brightness, identity). Pose afterwards is measured *relative* to
   that baseline, so a camera mounted high or low does not matter.
2. COLLECTING   a frame is accepted only when exactly one face passes the
   detection, quality, liveness and identity gates, the pose has been stable
   for a short dwell, it maps to a coverage bucket that still needs samples,
   and it is not a duplicate of anything already accepted.
3. COMPLETE     every required bucket reached its minimum count and enough
   samples exist overall. Nothing is written yet.
4. ``finalize()`` re-checks liveness / diversity / coverage on the final pool,
   prunes inconsistent samples, selects up to ``max_templates`` diverse
   templates and returns a ``CoverageResult``. Only an ``ok`` result may be
   committed to disk (see ``enrollment_commit.py``).

Privacy: only embeddings and scalar pose numbers are kept; ``cancel()`` /
``discard()`` zero every stored embedding.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

# --------------------------------------------------------------------------
# Pose from the five InsightFace landmarks
# --------------------------------------------------------------------------


def pose_from_5pt(kps) -> Optional[tuple[float, float, float]]:
    """(yaw, pitch, roll) from five landmarks; yaw/pitch ~[-1, 1], roll degrees.

    Negative yaw = user's left, negative pitch = chin up (same convention the
    guided enrollment has always used). Returns None if landmarks are unusable.
    """
    if kps is None:
        return None
    try:
        pts = np.asarray(kps, dtype=np.float32)
        if pts.shape != (5, 2) or not np.all(np.isfinite(pts)):
            return None
        l_eye, r_eye, nose, l_mouth, r_mouth = pts
        eye_dx = float(r_eye[0] - l_eye[0])
        eye_dy = float(r_eye[1] - l_eye[1])
        inter_eye = math.hypot(eye_dx, eye_dy)
        if inter_eye < 4.0:
            return None
        eye_mid_x = (float(l_eye[0]) + float(r_eye[0])) * 0.5
        eye_mid_y = (float(l_eye[1]) + float(r_eye[1])) * 0.5
        mouth_mid_y = (float(l_mouth[1]) + float(r_mouth[1])) * 0.5
        face_vert = max(4.0, mouth_mid_y - eye_mid_y)
        yaw = -(float(nose[0]) - eye_mid_x) / (inter_eye * 0.52)
        pitch = ((float(nose[1]) - eye_mid_y) / face_vert - 0.56) / 0.24
        roll = math.degrees(math.atan2(eye_dy, eye_dx))
        return (
            float(np.clip(yaw, -1.0, 1.0)),
            float(np.clip(pitch, -1.0, 1.0)),
            float(np.clip(roll, -45.0, 45.0)),
        )
    except Exception:
        return None


# --------------------------------------------------------------------------
# Buckets
# --------------------------------------------------------------------------

CENTER = "center"
LEFT_MILD, LEFT_WIDE = "left_mild", "left_wide"
RIGHT_MILD, RIGHT_WIDE = "right_mild", "right_wide"
UP, DOWN = "up", "down"
UP_LEFT, UP_RIGHT = "up_left", "up_right"
DOWN_LEFT, DOWN_RIGHT = "down_left", "down_right"
TILT_LEFT, TILT_RIGHT = "tilt_left", "tilt_right"
NEAR, FAR, DIM, BRIGHT = "near", "far", "dim", "bright"

POSE_BUCKETS = (
    CENTER, LEFT_MILD, LEFT_WIDE, RIGHT_MILD, RIGHT_WIDE, UP, DOWN,
    UP_LEFT, UP_RIGHT, DOWN_LEFT, DOWN_RIGHT, TILT_LEFT, TILT_RIGHT,
)
CONTEXT_BUCKETS = (NEAR, FAR, DIM, BRIGHT)
ALL_BUCKETS = POSE_BUCKETS + CONTEXT_BUCKETS

# Required for completion. Vertical buckets are only required when the pitch
# estimate proved stable during calibration.
_REQUIRED_ALWAYS = (CENTER, LEFT_MILD, LEFT_WIDE, RIGHT_MILD, RIGHT_WIDE)
_REQUIRED_VERTICAL = (UP, DOWN, UP_LEFT, UP_RIGHT)
# Directly reachable without vertical pose.
_HORIZONTAL_ONLY = (CENTER, LEFT_MILD, LEFT_WIDE, RIGHT_MILD, RIGHT_WIDE)

# Where each bucket sits on the ring (degrees, 0 = right, counter-clockwise,
# 90 = top) and which radial layer it uses (0 inner, 1 outer, 2 centre).
SECTOR_ANGLE = {
    RIGHT_MILD: (0.0, 0), RIGHT_WIDE: (0.0, 1),
    UP_RIGHT: (45.0, 1), UP: (90.0, 1), UP_LEFT: (135.0, 1),
    LEFT_MILD: (180.0, 0), LEFT_WIDE: (180.0, 1),
    DOWN_LEFT: (225.0, 1), DOWN: (270.0, 1), DOWN_RIGHT: (315.0, 1),
    CENTER: (0.0, 2),
    TILT_LEFT: (160.0, 2), TILT_RIGHT: (20.0, 2),
    NEAR: (270.0, 2), FAR: (90.0, 2), DIM: (225.0, 2), BRIGHT: (315.0, 2),
}

CUE_FOR_BUCKET = {
    CENTER: "center", LEFT_MILD: "left", LEFT_WIDE: "left", RIGHT_MILD: "right",
    RIGHT_WIDE: "right", UP: "up", DOWN: "down", UP_LEFT: "up_left",
    UP_RIGHT: "up_right", DOWN_LEFT: "down_left", DOWN_RIGHT: "down_right",
    TILT_LEFT: "tilt_left", TILT_RIGHT: "tilt_right",
    NEAR: "closer", FAR: "back", DIM: "", BRIGHT: "",
}


def assign_bucket(
    rel_yaw: float,
    rel_pitch: float,
    rel_roll: float = 0.0,
    *,
    pitch_reliable: bool = True,
    dead_lo: float = 0.10,
    dead_hi: float = 0.12,
    wide_yaw: float = 0.38,
    tilt_deg: float = 8.0,
) -> Optional[str]:
    """Map a baseline-relative pose to a pose bucket, or None in a dead zone.

    The 0.10-0.12 band between "neutral" and "turned" belongs to no bucket
    (hysteresis) so noise at a boundary cannot flip a sample between buckets.
    """
    ry = float(rel_yaw)
    rp = float(rel_pitch) if pitch_reliable else 0.0
    ay, ap = abs(ry), abs(rp)

    if ay < dead_lo and ap < dead_lo:
        if abs(rel_roll) >= tilt_deg:
            return TILT_LEFT if rel_roll > 0 else TILT_RIGHT
        return CENTER
    if dead_lo <= ay < dead_hi or dead_lo <= ap < dead_hi:
        return None

    horiz = ""
    if ay >= dead_hi:
        horiz = "left" if ry < 0 else "right"
    vert = ""
    if ap >= dead_hi:
        vert = "up" if rp < 0 else "down"

    if horiz and vert:
        return f"{vert}_{horiz}"
    if vert:
        return vert
    if horiz:
        return f"{horiz}_wide" if ay >= wide_yaw else f"{horiz}_mild"
    return None


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass
class Observation:
    """What the runner measured on one camera frame."""

    t: float                       # monotonic seconds
    frame_id: int
    face_count: int
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    det_score: float = 0.0
    quality: float = 0.0
    height_frac: float = 0.0
    illumination: float = 0.0
    live_score: Optional[float] = None  # None: liveness was not evaluated
    embedding: Optional[np.ndarray] = None
    framing: str = ""              # "" ok, else move_closer / move_back / center


@dataclass
class CoverageSample:
    embedding: np.ndarray
    bucket: str
    rel_yaw: float
    rel_pitch: float
    rel_roll: float
    quality: float
    det_score: float
    height_frac: float
    illumination: float
    live_score: float
    t: float
    cohesion: float = 0.0
    selected: bool = False

    def metadata(self) -> dict:
        data = asdict(self)
        data.pop("embedding", None)
        return data


@dataclass
class CoverageConfig:
    calibration_s: float = 0.75
    calibration_min_frames: int = 5
    calibration_max_pose_spread: float = 0.12
    calibration_min_identity_cos: float = 0.60
    calibration_max_gap_s: float = 0.50
    pitch_noise_max: float = 0.06       # calibration pitch spread allowed for vertical buckets

    min_det: float = 0.70
    min_quality: float = 0.62
    liveness_threshold: float = 0.70    # never lower than the enrollment floor
    min_identity_cos: float = 0.35      # vs. the calibrated baseline identity

    dwell_s: float = 0.20
    dwell_min_frames: int = 2
    dwell_pose_tol: float = 0.06

    dup_similarity: float = 0.985
    bucket_pose_gap: float = 0.05
    bucket_emb_gap_sim: float = 0.93
    min_interval_s: float = 0.15

    near_ratio: float = 1.18
    far_ratio: float = 0.85
    dim_ratio: float = 0.72
    bright_ratio: float = 1.35

    min_total: int = 50
    max_templates: int = 60
    min_cohesion: float = 0.45
    cohesion_top_k: int = 3

    def quotas(self, pitch_reliable: bool) -> dict[str, int]:
        """Per-bucket (cap, minimum). Caps stop one pose dominating the pool."""
        if pitch_reliable:
            caps = {CENTER: 14, LEFT_MILD: 8, RIGHT_MILD: 8, LEFT_WIDE: 6, RIGHT_WIDE: 6,
                    UP: 5, DOWN: 5, UP_LEFT: 6, UP_RIGHT: 6}
        else:
            caps = {CENTER: 20, LEFT_MILD: 12, RIGHT_MILD: 12, LEFT_WIDE: 9, RIGHT_WIDE: 9}
        for name in ALL_BUCKETS:
            caps.setdefault(name, 4)
        return caps

    def minimums(self, pitch_reliable: bool) -> dict[str, int]:
        mins = {CENTER: 6, LEFT_MILD: 4, RIGHT_MILD: 4, LEFT_WIDE: 3, RIGHT_WIDE: 3}
        if pitch_reliable:
            mins.update({UP: 3, DOWN: 3, UP_LEFT: 3, UP_RIGHT: 3})
        return mins


CALIBRATING, COLLECTING, COMPLETE, CANCELLED = "calibrating", "collecting", "complete", "cancelled"


@dataclass
class Feedback:
    state: str
    progress: float                 # 0..1, monotonic
    accepted: bool = False
    reason: str = ""                # why the frame was skipped ("" when accepted)
    bucket: Optional[str] = None
    cue: str = ""                   # direction hint for the UI
    target: Optional[str] = None    # bucket the cue steers toward
    total: int = 0
    calibration: float = 0.0        # 0..1
    fill: dict = field(default_factory=dict)  # bucket -> 0..1 for required buckets


@dataclass
class CoverageResult:
    ok: bool
    reason: str = ""
    selected: list = field(default_factory=list)
    captured_count: int = 0
    pruned_count: int = 0
    bucket_counts: dict = field(default_factory=dict)
    pitch_reliable: bool = True

    def embeddings(self) -> np.ndarray:
        if not self.selected:
            return np.zeros((0, 0), dtype=np.float32)
        return np.stack([s.embedding for s in self.selected]).astype(np.float32)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _unit(vec) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32).ravel()
    n = float(np.linalg.norm(arr))
    if not math.isfinite(n) or n < 1e-8:
        return np.zeros_like(arr)
    return arr / n


def _zero(arr) -> None:
    if isinstance(arr, np.ndarray) and arr.flags.writeable:
        arr.fill(0)


def sample_quality(quality: float, height_frac: float) -> float:
    """Same weighting the burst collector used: frame quality + face size."""
    size = float(np.clip(1.0 - abs(height_frac - 0.50) / 0.30, 0.0, 1.0))
    return float(quality * 0.75 + size * 0.25)


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------


class CoverageCollector:
    def __init__(self, config: Optional[CoverageConfig] = None) -> None:
        self.config = config or CoverageConfig()
        self.state = CALIBRATING
        self.samples: list[CoverageSample] = []
        self.pitch_reliable = True

        self._cal: list[Observation] = []
        self._base_yaw = self._base_pitch = self._base_roll = 0.0
        self._base_height = self._base_illum = 0.0
        self._base_emb: Optional[np.ndarray] = None

        self._anchor: Optional[tuple[float, float]] = None
        self._anchor_t = 0.0
        self._anchor_frames = 0
        self._last_frame_id: Optional[int] = None
        self._last_accept_t = -1e9
        self._progress = 0.0

    # ---- public ----------------------------------------------------------

    @property
    def baseline(self) -> dict:
        return {
            "yaw": self._base_yaw, "pitch": self._base_pitch, "roll": self._base_roll,
            "height_frac": self._base_height, "illumination": self._base_illum,
        }

    def counts(self) -> dict[str, int]:
        out = {name: 0 for name in ALL_BUCKETS}
        for s in self.samples:
            out[s.bucket] = out.get(s.bucket, 0) + 1
        return out

    def required_buckets(self) -> tuple[str, ...]:
        return _REQUIRED_ALWAYS + (_REQUIRED_VERTICAL if self.pitch_reliable else ())

    def is_complete(self) -> bool:
        if self.state == CANCELLED:
            return False
        counts = self.counts()
        mins = self.config.minimums(self.pitch_reliable)
        covered = all(counts.get(b, 0) >= mins[b] for b in self.required_buckets())
        return covered and len(self.samples) >= self.config.min_total

    def cancel(self) -> None:
        self.discard()
        self.state = CANCELLED

    def discard(self) -> None:
        """Zero and drop every stored embedding / calibration frame."""
        for s in self.samples:
            _zero(s.embedding)
        self.samples.clear()
        for o in self._cal:
            _zero(o.embedding)
        self._cal.clear()
        _zero(self._base_emb)
        self._base_emb = None

    def feed(self, obs: Observation) -> Feedback:
        if self.state == CANCELLED:
            return self._fb(False, "cancelled")
        if self.state == COMPLETE:
            return self._fb(False, "complete")
        if self.state == CALIBRATING:
            return self._feed_calibration(obs)
        return self._feed_collecting(obs)

    # ---- validity gate ---------------------------------------------------

    def _gate(self, obs: Observation) -> str:
        c = self.config
        if obs.face_count == 0:
            return "no_face"
        if obs.face_count > 1:
            return "multiple_faces"
        if obs.embedding is None or not np.all(np.isfinite(obs.embedding)):
            return "no_embedding"
        if obs.framing:
            return obs.framing
        if obs.det_score < c.min_det:
            return "detection"
        if obs.quality < c.min_quality:
            return "quality"
        if obs.live_score is None or obs.live_score < c.liveness_threshold:
            return "liveness"
        return ""

    # ---- calibration -----------------------------------------------------

    def _feed_calibration(self, obs: Observation) -> Feedback:
        c = self.config
        reason = self._gate(obs)
        if reason:
            self._drop_calibration(obs)
            return self._fb(False, reason)

        if self._cal and obs.t - self._cal[-1].t > c.calibration_max_gap_s:
            self._drop_calibration(None)
        keep = Observation(**{**obs.__dict__, "embedding": _unit(obs.embedding)})
        self._cal.append(keep)

        yaws = [o.yaw for o in self._cal]
        pitches = [o.pitch for o in self._cal]
        spread = max(max(yaws) - min(yaws), max(pitches) - min(pitches))
        if spread > c.calibration_max_pose_spread:
            self._drop_calibration(None)
            self._cal.append(Observation(**{**obs.__dict__, "embedding": _unit(obs.embedding)}))
            return self._fb(False, "hold_still")

        elapsed = self._cal[-1].t - self._cal[0].t
        if elapsed >= c.calibration_s and len(self._cal) >= c.calibration_min_frames:
            if self._finish_calibration():
                return self._fb(False, "calibrated")
            return self._fb(False, "identity_unstable")
        return self._fb(False, "calibrating")

    def _drop_calibration(self, _obs) -> None:
        for o in self._cal:
            _zero(o.embedding)
        self._cal.clear()

    def _finish_calibration(self) -> bool:
        c = self.config
        embs = np.stack([o.embedding for o in self._cal]).astype(np.float32)
        centre = _unit(embs.mean(axis=0))
        sims = embs @ centre
        if float(sims.min()) < c.calibration_min_identity_cos:
            self._drop_calibration(None)
            return False
        self._base_emb = centre
        self._base_yaw = float(np.median([o.yaw for o in self._cal]))
        self._base_pitch = float(np.median([o.pitch for o in self._cal]))
        self._base_roll = float(np.median([o.roll for o in self._cal]))
        self._base_height = float(np.median([o.height_frac for o in self._cal]))
        self._base_illum = float(np.median([o.illumination for o in self._cal]))
        pitch_spread = max(o.pitch for o in self._cal) - min(o.pitch for o in self._cal)
        self.pitch_reliable = pitch_spread <= c.pitch_noise_max
        self._drop_calibration(None)
        self.state = COLLECTING
        return True

    # ---- collecting ------------------------------------------------------

    def _reset_dwell(self) -> None:
        self._anchor = None
        self._anchor_frames = 0

    def _feed_collecting(self, obs: Observation) -> Feedback:
        c = self.config
        reason = self._gate(obs)
        if reason:
            self._reset_dwell()
            return self._fb(False, reason)

        emb = _unit(obs.embedding)
        if float(emb @ self._base_emb) < c.min_identity_cos:
            self._reset_dwell()
            _zero(emb)
            return self._fb(False, "identity_mismatch")

        ry = obs.yaw - self._base_yaw
        rp = obs.pitch - self._base_pitch
        rr = obs.roll - self._base_roll

        # dwell: pose must stay within tolerance of an anchor for a short time
        if self._anchor is None or math.hypot(ry - self._anchor[0], rp - self._anchor[1]) > c.dwell_pose_tol:
            self._anchor = (ry, rp)
            self._anchor_t = obs.t
            self._anchor_frames = 1
        else:
            self._anchor_frames += 1
        if self._anchor_frames < c.dwell_min_frames or obs.t - self._anchor_t < c.dwell_s:
            _zero(emb)
            return self._fb(False, "settling")

        bucket = assign_bucket(ry, rp, rr, pitch_reliable=self.pitch_reliable)
        bucket = self._context_bucket(bucket, obs)
        if bucket is None:
            _zero(emb)
            return self._fb(False, "no_bucket")

        caps = c.quotas(self.pitch_reliable)
        if self._count(bucket) >= caps.get(bucket, 4):
            _zero(emb)
            return self._fb(False, "bucket_full", bucket)

        why = self._diversity_reject(obs, emb, bucket, ry, rp)
        if why:
            _zero(emb)
            return self._fb(False, why, bucket)

        self.samples.append(
            CoverageSample(
                embedding=emb.copy(), bucket=bucket, rel_yaw=ry, rel_pitch=rp, rel_roll=rr,
                quality=sample_quality(obs.quality, obs.height_frac), det_score=obs.det_score,
                height_frac=obs.height_frac, illumination=obs.illumination,
                live_score=float(obs.live_score), t=obs.t,
            )
        )
        _zero(emb)
        self._last_frame_id = obs.frame_id
        self._last_accept_t = obs.t
        if self.is_complete():
            self.state = COMPLETE
        return self._fb(True, "", bucket)

    def _context_bucket(self, bucket: Optional[str], obs: Observation) -> Optional[str]:
        if bucket not in (CENTER, LEFT_MILD, RIGHT_MILD):
            return bucket
        c = self.config
        if self._base_height > 0:
            ratio = obs.height_frac / self._base_height
            if ratio >= c.near_ratio:
                return NEAR
            if ratio <= c.far_ratio:
                return FAR
        if self._base_illum > 0:
            ratio = obs.illumination / self._base_illum
            if ratio <= c.dim_ratio:
                return DIM
            if ratio >= c.bright_ratio:
                return BRIGHT
        return bucket

    def _count(self, bucket: str) -> int:
        return sum(1 for s in self.samples if s.bucket == bucket)

    def _diversity_reject(self, obs, emb, bucket, ry, rp) -> str:
        c = self.config
        if self._last_frame_id is not None and obs.frame_id == self._last_frame_id:
            return "duplicate_frame"
        if obs.t - self._last_accept_t < c.min_interval_s:
            return "too_soon"
        if not self.samples:
            return ""
        mat = np.stack([s.embedding for s in self.samples])
        sims = mat @ emb
        if float(sims.max()) >= c.dup_similarity:
            return "duplicate_embedding"
        for s, sim in zip(self.samples, sims):
            if s.bucket != bucket:
                continue
            if math.hypot(ry - s.rel_yaw, rp - s.rel_pitch) < c.bucket_pose_gap and float(sim) > c.bucket_emb_gap_sim:
                return "too_similar"
        return ""

    # ---- feedback --------------------------------------------------------

    def _fill(self) -> dict:
        counts = self.counts()
        mins = self.config.minimums(self.pitch_reliable)
        return {b: min(1.0, counts.get(b, 0) / mins[b]) for b in self.required_buckets()}

    def _compute_progress(self) -> float:
        if self.state == CALIBRATING:
            return 0.0
        if self.state == COMPLETE:
            return 1.0
        counts = self.counts()
        mins = self.config.minimums(self.pitch_reliable)
        need = sum(mins[b] for b in self.required_buckets()) + self.config.min_total
        have = sum(min(counts.get(b, 0), mins[b]) for b in self.required_buckets())
        have += min(len(self.samples), self.config.min_total)
        return min(0.999, have / need)

    def next_target(self) -> Optional[str]:
        """Bucket the user should be steered toward next (None when idle/done)."""
        if self.state != COLLECTING:
            return None
        counts = self.counts()
        mins = self.config.minimums(self.pitch_reliable)
        caps = self.config.quotas(self.pitch_reliable)
        open_required = [b for b in self.required_buckets() if counts.get(b, 0) < mins[b]]
        if open_required:
            return min(open_required, key=lambda b: counts.get(b, 0) / mins[b])
        # coverage satisfied but still short on volume: ask for more variety
        room = [b for b in self.required_buckets() if counts.get(b, 0) < caps[b]]
        if room:
            return min(room, key=lambda b: counts.get(b, 0) / caps[b])
        return None

    def next_cue(self) -> str:
        target = self.next_target()
        return CUE_FOR_BUCKET.get(target, "") if target else ""

    def _calibration_fraction(self) -> float:
        if self.state != CALIBRATING:
            return 1.0
        if not self._cal:
            return 0.0
        return float(min(1.0, (self._cal[-1].t - self._cal[0].t) / self.config.calibration_s))

    def _fb(self, accepted: bool, reason: str = "", bucket: Optional[str] = None) -> Feedback:
        self._progress = max(self._progress, self._compute_progress())
        return Feedback(
            state=self.state, progress=self._progress, accepted=accepted, reason=reason,
            bucket=bucket, cue=self.next_cue(), target=self.next_target(), total=len(self.samples),
            calibration=self._calibration_fraction(),
            fill=self._fill() if self.state != CALIBRATING else {},
        )

    # ---- finalize --------------------------------------------------------

    def finalize(self) -> CoverageResult:
        """Validate the finished pool and pick the gallery. Never writes disk."""
        c = self.config
        captured = len(self.samples)
        base = dict(captured_count=captured, pitch_reliable=self.pitch_reliable)
        if self.state != COMPLETE:
            return CoverageResult(False, "coverage_incomplete", **base)
        if any(s.live_score < c.liveness_threshold for s in self.samples):
            return CoverageResult(False, "liveness_failed", **base)

        pool = self._prune(list(self.samples))
        pruned = captured - len(pool)
        counts: dict[str, int] = {}
        for s in pool:
            counts[s.bucket] = counts.get(s.bucket, 0) + 1
        mins = c.minimums(self.pitch_reliable)
        if any(counts.get(b, 0) < mins[b] for b in self.required_buckets()):
            return CoverageResult(False, "coverage_lost_after_pruning", pruned_count=pruned,
                                  bucket_counts=counts, **base)

        selected = select_diverse(pool, c.max_templates, mins, c.dup_similarity)
        sel_counts: dict[str, int] = {}
        for s in selected:
            sel_counts[s.bucket] = sel_counts.get(s.bucket, 0) + 1
        if len(selected) < c.min_total:
            return CoverageResult(False, "insufficient_diversity", pruned_count=pruned,
                                  bucket_counts=sel_counts, **base)
        if any(sel_counts.get(b, 0) < mins[b] for b in self.required_buckets()):
            return CoverageResult(False, "selection_undercovered", pruned_count=pruned,
                                  bucket_counts=sel_counts, **base)
        mat = np.stack([s.embedding for s in selected])
        gram = mat @ mat.T
        np.fill_diagonal(gram, -1.0)
        if float(gram.max()) >= c.dup_similarity:
            return CoverageResult(False, "duplicate_templates", pruned_count=pruned,
                                  bucket_counts=sel_counts, **base)

        for s in self.samples:
            s.selected = False
        for s in selected:
            s.selected = True
        return CoverageResult(True, "", selected=selected, pruned_count=pruned,
                              bucket_counts=sel_counts, **base)

    def _prune(self, pool: list[CoverageSample]) -> list[CoverageSample]:
        c = self.config
        if len(pool) <= 1:
            return pool
        mat = np.stack([s.embedding for s in pool])
        sims = mat @ mat.T
        np.fill_diagonal(sims, -1.0)
        k = min(c.cohesion_top_k, max(1, len(pool) - 1))
        coh = np.mean(np.sort(sims, axis=1)[:, -k:], axis=1)
        for s, v in zip(pool, coh.tolist()):
            s.cohesion = float(v)
        return [s for s in pool if s.cohesion >= c.min_cohesion]


# --------------------------------------------------------------------------
# Diverse selection
# --------------------------------------------------------------------------


def select_diverse(
    pool: list[CoverageSample],
    max_templates: int,
    min_per_bucket: dict[str, int],
    dup_similarity: float,
) -> list[CoverageSample]:
    """Per-bucket floor first, then greedy quality+diversity; no near-duplicates."""
    if not pool:
        return []
    limit = max(1, min(int(max_templates), len(pool)))
    mat = np.stack([s.embedding for s in pool])
    chosen: list[int] = []

    def max_sim(idx: int) -> float:
        if not chosen:
            return 0.0
        return float(np.max(mat[chosen] @ mat[idx]))

    def best(candidates: list[int]) -> Optional[int]:
        top, top_rank = None, -1e9
        for i in candidates:
            if i in chosen:
                continue
            ms = max_sim(i)
            if chosen and ms >= dup_similarity:
                continue
            rank = pool[i].quality * 0.65 + (1.0 - ms) * 0.35
            if rank > top_rank:
                top, top_rank = i, rank
        return top

    by_bucket: dict[str, list[int]] = {}
    for i, s in enumerate(pool):
        by_bucket.setdefault(s.bucket, []).append(i)

    for bucket, need in min_per_bucket.items():
        members = by_bucket.get(bucket, [])
        for _ in range(need):
            if len(chosen) >= limit:
                break
            pick = best(members)
            if pick is None:
                break
            chosen.append(pick)

    while len(chosen) < limit:
        pick = best(list(range(len(pool))))
        if pick is None:
            break
        chosen.append(pick)
    return [pool[i] for i in chosen]
