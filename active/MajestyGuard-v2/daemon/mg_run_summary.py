"""
Offline MajestyGuard daemon log summarizer.

Reads daemon text logs and reports run stability metrics. It does not open the
camera, start IPC, call lock APIs, or touch machine state.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable


_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_STATE_RE = re.compile(r"STATE: (?P<old>[A-Z_]+) -> (?P<new>[A-Z_]+)")
_ACTIVE_FRAME_RE = re.compile(r"\bActive frame=\d+\b")
_SCANNING_FRAME_RE = re.compile(r"\bScanning frame=\d+\b")
_KEY_VALUE_RE = re.compile(r"\b(?P<key>[a-z_]+)=(?P<value>[A-Za-z0-9_.+-]+)")
_LOW_SCORE_REASON_RE = re.compile(r"low-score face treated as uncertain reason=(?P<reason>[a-z_]+)")
_ACTIVE_CONTINUITY_RE = re.compile(r"owner-continuity dip held active")
_DEFINITE_STRANGER_RE = re.compile(r"(?P<state>Scanning|Active): definite stranger")


@dataclass(frozen=True)
class NumericStats:
    count: int
    min: float | None
    median: float | None
    max: float | None


@dataclass(frozen=True)
class RunSummary:
    first_timestamp: str | None
    last_timestamp: str | None
    duration_seconds: float
    entered_active: bool
    active_hold_seconds: float
    state_transitions: dict[str, int]
    social_lock_count: int
    locked_count: int
    lock_suppressed_count: int
    low_score_reasons: dict[str, int]
    scanning_score: NumericStats
    scanning_liveness: NumericStats
    scanning_quality: NumericStats
    scanning_smooth: NumericStats
    scanning_presence: NumericStats
    scanning_no_face_frame_count: int
    active_score: NumericStats
    active_liveness: NumericStats
    active_quality: NumericStats
    active_no_face_frame_count: int
    active_smooth: NumericStats
    active_presence: NumericStats
    active_raw_faces: NumericStats
    active_face_height: NumericStats
    active_center_offset: NumericStats
    active_sticky_iou: NumericStats
    active_kalman_iou: NumericStats
    active_inference_ms: NumericStats
    selection_reasons: dict[str, int]
    template_hits: dict[int, int]
    low_score_score: NumericStats
    low_score_smooth: NumericStats
    low_score_liveness: NumericStats
    low_score_quality: NumericStats
    low_score_face_height: NumericStats
    low_score_center_offset: NumericStats
    active_continuity_hold_count: int
    active_continuity_reasons: dict[str, int]
    active_continuity_score: NumericStats
    active_continuity_smooth: NumericStats
    active_continuity_liveness: NumericStats
    active_continuity_quality: NumericStats
    active_continuity_face_height: NumericStats
    active_continuity_center_offset: NumericStats
    definite_stranger_count: int
    definite_stranger_reasons: dict[str, int]
    definite_stranger_score: NumericStats
    definite_stranger_smooth: NumericStats
    definite_stranger_liveness: NumericStats
    definite_stranger_quality: NumericStats
    definite_stranger_face_height: NumericStats
    definite_stranger_center_offset: NumericStats
    definite_stranger_sticky_iou: NumericStats
    definite_stranger_kalman_iou: NumericStats


@dataclass(frozen=True)
class AssessmentCheck:
    name: str
    ok: bool
    observed: float | int | bool | None
    required: float | int | bool
    message: str


@dataclass(frozen=True)
class RunAssessment:
    ok: bool
    checks: list[AssessmentCheck]


@dataclass(frozen=True)
class RepeatedRunAssessment:
    ok: bool
    run_count: int
    checks: list[AssessmentCheck]
    run_assessments: list[RunAssessment]


def _parse_timestamp(line: str) -> datetime | None:
    match = _TS_RE.search(line)
    if not match:
        return None
    return datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S,%f")


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _stats(values: list[float]) -> NumericStats:
    if not values:
        return NumericStats(count=0, min=None, median=None, max=None)
    return NumericStats(
        count=len(values),
        min=min(values),
        median=float(median(values)),
        max=max(values),
    )


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _fields(line: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in _KEY_VALUE_RE.finditer(line)}


def _float_field(fields: dict[str, str], key: str) -> float | None:
    raw = fields.get(key)
    if raw is None:
        return None
    if raw.endswith("ms"):
        raw = raw[:-2]
    try:
        return float(raw)
    except ValueError:
        return None


def _int_field(fields: dict[str, str], key: str) -> int | None:
    raw = fields.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _append_float(fields: dict[str, str], key: str, values: list[float]) -> None:
    value = _float_field(fields, key)
    if value is not None:
        values.append(value)


def summarize_lines(lines: Iterable[str]) -> RunSummary:
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    active_started_at: datetime | None = None
    active_hold_seconds = 0.0
    entered_active = False

    state_transitions: dict[str, int] = {}
    low_score_reasons: dict[str, int] = {}
    scanning_scores: list[float] = []
    scanning_liveness_scores: list[float] = []
    scanning_qualities: list[float] = []
    scanning_smoothed_scores: list[float] = []
    scanning_presence_scores: list[float] = []
    scores: list[float] = []
    liveness_scores: list[float] = []
    qualities: list[float] = []
    smoothed_scores: list[float] = []
    presence_scores: list[float] = []
    active_raw_faces: list[float] = []
    active_face_heights: list[float] = []
    active_center_offsets: list[float] = []
    active_sticky_ious: list[float] = []
    active_kalman_ious: list[float] = []
    active_inference_ms: list[float] = []
    selection_reasons: dict[str, int] = {}
    template_hits: dict[int, int] = {}
    low_score_scores: list[float] = []
    low_score_smooth: list[float] = []
    low_score_liveness: list[float] = []
    low_score_quality: list[float] = []
    low_score_face_heights: list[float] = []
    low_score_center_offsets: list[float] = []
    active_continuity_scores: list[float] = []
    active_continuity_smooth: list[float] = []
    active_continuity_liveness: list[float] = []
    active_continuity_quality: list[float] = []
    active_continuity_face_heights: list[float] = []
    active_continuity_center_offsets: list[float] = []
    active_continuity_reasons: dict[str, int] = {}
    definite_stranger_scores: list[float] = []
    definite_stranger_smooth: list[float] = []
    definite_stranger_liveness: list[float] = []
    definite_stranger_quality: list[float] = []
    definite_stranger_face_heights: list[float] = []
    definite_stranger_center_offsets: list[float] = []
    definite_stranger_sticky_ious: list[float] = []
    definite_stranger_kalman_ious: list[float] = []
    definite_stranger_reasons: dict[str, int] = {}
    social_lock_count = 0
    locked_count = 0
    lock_suppressed_count = 0
    scanning_no_face_frame_count = 0
    active_no_face_frame_count = 0
    active_continuity_hold_count = 0
    definite_stranger_count = 0
    last_transition: str | None = None
    last_transition_ts: datetime | None = None

    for line in lines:
        ts = _parse_timestamp(line)
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        state = _STATE_RE.search(line)
        if state:
            transition = f"{state.group('old')}->{state.group('new')}"
            duplicate_transition = (
                transition == last_transition
                and ts is not None
                and last_transition_ts is not None
                and (ts - last_transition_ts).total_seconds() <= 1.0
            )
            if not duplicate_transition:
                _increment(state_transitions, transition)
                last_transition = transition
                last_transition_ts = ts
            new_state = state.group("new")
            if not duplicate_transition:
                if new_state == "ACTIVE" and ts is not None:
                    entered_active = True
                    active_started_at = ts
                elif active_started_at is not None and ts is not None:
                    active_hold_seconds += max(0.0, (ts - active_started_at).total_seconds())
                    active_started_at = None
                if new_state == "SOCIAL_LOCK":
                    social_lock_count += 1
                elif new_state == "LOCKED":
                    locked_count += 1

        fields = _fields(line)

        scanning_frame = _SCANNING_FRAME_RE.search(line)
        if scanning_frame:
            faces = _int_field(fields, "faces")
            if faces is not None and faces <= 0:
                scanning_no_face_frame_count += 1
            else:
                _append_float(fields, "score", scanning_scores)
                _append_float(fields, "liveness", scanning_liveness_scores)
                _append_float(fields, "quality", scanning_qualities)
                _append_float(fields, "smooth", scanning_smoothed_scores)
                _append_float(fields, "presence", scanning_presence_scores)
                reason = fields.get("reason")
                if reason:
                    _increment(selection_reasons, reason)
                template = _int_field(fields, "template")
                if template is not None and template >= 0:
                    _increment(template_hits, template)

        active_frame = _ACTIVE_FRAME_RE.search(line)
        if active_frame:
            faces = _int_field(fields, "faces")
            if faces is not None and faces <= 0:
                active_no_face_frame_count += 1
                continue
            _append_float(fields, "score", scores)
            _append_float(fields, "liveness", liveness_scores)
            _append_float(fields, "quality", qualities)
            _append_float(fields, "smooth", smoothed_scores)
            _append_float(fields, "presence", presence_scores)
            _append_float(fields, "raw_faces", active_raw_faces)
            _append_float(fields, "face_h", active_face_heights)
            _append_float(fields, "center", active_center_offsets)
            _append_float(fields, "sticky_iou", active_sticky_ious)
            _append_float(fields, "kalman_iou", active_kalman_ious)
            _append_float(fields, "inference", active_inference_ms)
            reason = fields.get("reason")
            if reason:
                _increment(selection_reasons, reason)
            template = _int_field(fields, "template")
            if template is not None and template >= 0:
                _increment(template_hits, template)

        low_score = _LOW_SCORE_REASON_RE.search(line)
        if low_score:
            _increment(low_score_reasons, low_score.group("reason"))
            _append_float(fields, "score", low_score_scores)
            _append_float(fields, "smooth", low_score_smooth)
            _append_float(fields, "liveness", low_score_liveness)
            _append_float(fields, "quality", low_score_quality)
            _append_float(fields, "face_h", low_score_face_heights)
            _append_float(fields, "center", low_score_center_offsets)

        if _ACTIVE_CONTINUITY_RE.search(line):
            active_continuity_hold_count += 1
            reason = fields.get("reason")
            if reason:
                _increment(active_continuity_reasons, reason)
            _append_float(fields, "score", active_continuity_scores)
            _append_float(fields, "smooth", active_continuity_smooth)
            _append_float(fields, "liveness", active_continuity_liveness)
            _append_float(fields, "quality", active_continuity_quality)
            _append_float(fields, "face_h", active_continuity_face_heights)
            _append_float(fields, "center", active_continuity_center_offsets)

        definite_stranger = _DEFINITE_STRANGER_RE.search(line)
        if definite_stranger:
            definite_stranger_count += 1
            _increment(definite_stranger_reasons, definite_stranger.group("state").lower())
            _append_float(fields, "score", definite_stranger_scores)
            _append_float(fields, "smooth", definite_stranger_smooth)
            _append_float(fields, "liveness", definite_stranger_liveness)
            _append_float(fields, "quality", definite_stranger_quality)
            _append_float(fields, "face_h", definite_stranger_face_heights)
            _append_float(fields, "center", definite_stranger_center_offsets)
            _append_float(fields, "sticky_iou", definite_stranger_sticky_ious)
            _append_float(fields, "kalman_iou", definite_stranger_kalman_ious)

        if "LOCK SUPPRESSED" in line:
            lock_suppressed_count += 1

    if active_started_at is not None and last_ts is not None:
        active_hold_seconds += max(0.0, (last_ts - active_started_at).total_seconds())

    duration_seconds = 0.0
    if first_ts is not None and last_ts is not None:
        duration_seconds = max(0.0, (last_ts - first_ts).total_seconds())

    return RunSummary(
        first_timestamp=_timestamp_text(first_ts),
        last_timestamp=_timestamp_text(last_ts),
        duration_seconds=duration_seconds,
        entered_active=entered_active,
        active_hold_seconds=active_hold_seconds,
        state_transitions=state_transitions,
        social_lock_count=social_lock_count,
        locked_count=locked_count,
        lock_suppressed_count=lock_suppressed_count,
        low_score_reasons=low_score_reasons,
        scanning_score=_stats(scanning_scores),
        scanning_liveness=_stats(scanning_liveness_scores),
        scanning_quality=_stats(scanning_qualities),
        scanning_smooth=_stats(scanning_smoothed_scores),
        scanning_presence=_stats(scanning_presence_scores),
        scanning_no_face_frame_count=scanning_no_face_frame_count,
        active_score=_stats(scores),
        active_liveness=_stats(liveness_scores),
        active_quality=_stats(qualities),
        active_no_face_frame_count=active_no_face_frame_count,
        active_smooth=_stats(smoothed_scores),
        active_presence=_stats(presence_scores),
        active_raw_faces=_stats(active_raw_faces),
        active_face_height=_stats(active_face_heights),
        active_center_offset=_stats(active_center_offsets),
        active_sticky_iou=_stats(active_sticky_ious),
        active_kalman_iou=_stats(active_kalman_ious),
        active_inference_ms=_stats(active_inference_ms),
        selection_reasons=selection_reasons,
        template_hits=template_hits,
        low_score_score=_stats(low_score_scores),
        low_score_smooth=_stats(low_score_smooth),
        low_score_liveness=_stats(low_score_liveness),
        low_score_quality=_stats(low_score_quality),
        low_score_face_height=_stats(low_score_face_heights),
        low_score_center_offset=_stats(low_score_center_offsets),
        active_continuity_hold_count=active_continuity_hold_count,
        active_continuity_reasons=active_continuity_reasons,
        active_continuity_score=_stats(active_continuity_scores),
        active_continuity_smooth=_stats(active_continuity_smooth),
        active_continuity_liveness=_stats(active_continuity_liveness),
        active_continuity_quality=_stats(active_continuity_quality),
        active_continuity_face_height=_stats(active_continuity_face_heights),
        active_continuity_center_offset=_stats(active_continuity_center_offsets),
        definite_stranger_count=definite_stranger_count,
        definite_stranger_reasons=definite_stranger_reasons,
        definite_stranger_score=_stats(definite_stranger_scores),
        definite_stranger_smooth=_stats(definite_stranger_smooth),
        definite_stranger_liveness=_stats(definite_stranger_liveness),
        definite_stranger_quality=_stats(definite_stranger_quality),
        definite_stranger_face_height=_stats(definite_stranger_face_heights),
        definite_stranger_center_offset=_stats(definite_stranger_center_offsets),
        definite_stranger_sticky_iou=_stats(definite_stranger_sticky_ious),
        definite_stranger_kalman_iou=_stats(definite_stranger_kalman_ious),
    )


def summarize_file(path: str | Path) -> RunSummary:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text[:2000]:
            return summarize_lines(text.splitlines())

    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    return summarize_lines(text.splitlines())


def _check_at_least(name: str, observed, required, message: str) -> AssessmentCheck:
    return AssessmentCheck(
        name=name,
        ok=observed is not None and observed >= required,
        observed=observed,
        required=required,
        message=message,
    )


def _check_at_most(name: str, observed, required, message: str) -> AssessmentCheck:
    return AssessmentCheck(
        name=name,
        ok=observed is not None and observed <= required,
        observed=observed,
        required=required,
        message=message,
    )


def _check_equal(name: str, observed, required, message: str) -> AssessmentCheck:
    return AssessmentCheck(
        name=name,
        ok=observed == required,
        observed=observed,
        required=required,
        message=message,
    )


def _min_non_null(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def assess_summary(
    summary: RunSummary,
    *,
    min_active_seconds: float = 60.0,
    min_active_score_median: float = 0.70,
    min_active_presence_median: float = 0.70,
    min_active_liveness_median: float = 0.70,
    max_active_no_face_frames: int = 10,
    max_definite_stranger_frames: int = 0,
) -> RunAssessment:
    checks = [
        _check_equal(
            "entered_active",
            summary.entered_active,
            True,
            "Daemon should reacquire the enrolled owner and enter ACTIVE.",
        ),
        _check_at_least(
            "active_hold_seconds",
            summary.active_hold_seconds,
            min_active_seconds,
            "ACTIVE should remain stable for the requested run duration.",
        ),
        _check_equal(
            "social_lock_count",
            summary.social_lock_count,
            0,
            "Owner-only runs must not produce SOCIAL_LOCK.",
        ),
        _check_equal(
            "locked_count",
            summary.locked_count,
            0,
            "Lock-disabled hardening runs must not transition to LOCKED.",
        ),
        _check_at_most(
            "active_no_face_frame_count",
            summary.active_no_face_frame_count,
            max_active_no_face_frames,
            "Brief tracking misses are allowed, but sustained face loss needs investigation.",
        ),
        _check_at_most(
            "definite_stranger_count",
            summary.definite_stranger_count,
            max_definite_stranger_frames,
            "Owner-only runs should not accumulate definite-stranger evidence.",
        ),
        _check_at_least(
            "active_score_median",
            summary.active_score.median,
            min_active_score_median,
            "Median raw owner score should stay in the maintenance-safe band.",
        ),
        _check_at_least(
            "active_presence_median",
            summary.active_presence.median,
            min_active_presence_median,
            "Median presence confidence should remain stable during expression/motion dips.",
        ),
        _check_at_least(
            "active_liveness_median",
            summary.active_liveness.median,
            min_active_liveness_median,
            "Median liveness should remain above the calibrated RGB threshold.",
        ),
    ]
    return RunAssessment(ok=all(check.ok for check in checks), checks=checks)


def assess_repeated_owner_runs(
    summaries: list[RunSummary],
    *,
    min_runs: int = 3,
    min_active_seconds: float = 60.0,
    min_active_score_median: float = 0.70,
    min_active_presence_median: float = 0.70,
    min_active_liveness_median: float = 0.70,
    max_active_no_face_frames: int = 10,
    max_definite_stranger_frames: int = 0,
) -> RepeatedRunAssessment:
    run_assessments = [
        assess_summary(
            summary,
            min_active_seconds=min_active_seconds,
            min_active_score_median=min_active_score_median,
            min_active_presence_median=min_active_presence_median,
            min_active_liveness_median=min_active_liveness_median,
            max_active_no_face_frames=max_active_no_face_frames,
            max_definite_stranger_frames=max_definite_stranger_frames,
        )
        for summary in summaries
    ]
    total_social_lock = sum(summary.social_lock_count for summary in summaries)
    total_locked = sum(summary.locked_count for summary in summaries)
    total_definite_stranger = sum(summary.definite_stranger_count for summary in summaries)
    max_no_face = max((summary.active_no_face_frame_count for summary in summaries), default=None)
    min_active_hold = _min_non_null([summary.active_hold_seconds for summary in summaries])
    min_score_median = _min_non_null([summary.active_score.median for summary in summaries])
    min_presence_median = _min_non_null([summary.active_presence.median for summary in summaries])
    min_liveness_median = _min_non_null([summary.active_liveness.median for summary in summaries])

    checks = [
        _check_at_least(
            "run_count",
            len(summaries),
            min_runs,
            "Acceptance requires repeated summarized owner-only runs.",
        ),
        _check_equal(
            "all_runs_pass_owner_assessment",
            all(assessment.ok for assessment in run_assessments),
            True,
            "Every owner-only run must pass the single-run gate.",
        ),
        _check_equal(
            "total_social_lock_count",
            total_social_lock,
            0,
            "Repeated owner-only runs must have zero SOCIAL_LOCK transitions.",
        ),
        _check_equal(
            "total_locked_count",
            total_locked,
            0,
            "Lock-disabled repeated runs must have zero LOCKED transitions.",
        ),
        _check_at_most(
            "total_definite_stranger_count",
            total_definite_stranger,
            max_definite_stranger_frames,
            "Repeated owner-only runs should not accumulate definite-stranger evidence.",
        ),
        _check_at_most(
            "max_active_no_face_frame_count",
            max_no_face,
            max_active_no_face_frames,
            "No repeated owner run should have sustained face loss.",
        ),
        _check_at_least(
            "min_active_hold_seconds",
            min_active_hold,
            min_active_seconds,
            "Each accepted run should hold ACTIVE for the requested duration.",
        ),
        _check_at_least(
            "min_active_score_median",
            min_score_median,
            min_active_score_median,
            "Worst accepted run should maintain raw owner-score median.",
        ),
        _check_at_least(
            "min_active_presence_median",
            min_presence_median,
            min_active_presence_median,
            "Worst accepted run should maintain presence-confidence median.",
        ),
        _check_at_least(
            "min_active_liveness_median",
            min_liveness_median,
            min_active_liveness_median,
            "Worst accepted run should maintain liveness median.",
        ),
    ]
    return RepeatedRunAssessment(
        ok=all(check.ok for check in checks),
        run_count=len(summaries),
        checks=checks,
        run_assessments=run_assessments,
    )


def assess_attack_summary(
    summary: RunSummary,
    *,
    scenario: str,
    min_attack_evidence_frames: int = 3,
    min_social_lock_count: int = 1,
    max_spoof_liveness_median: float = 0.60,
    max_spoof_liveness_max: float = 0.70,
    max_attack_active_seconds: float = 0.0,
) -> RunAssessment:
    scenario = scenario.lower()
    if scenario == "stranger":
        checks = [
            _check_at_least(
                "definite_stranger_count",
                summary.definite_stranger_count,
                min_attack_evidence_frames,
                "Stranger runs must produce repeated definite-stranger evidence.",
            ),
            _check_at_least(
                "social_lock_count",
                summary.social_lock_count,
                min_social_lock_count,
                "A confirmed live stranger should reach SOCIAL_LOCK in lock-disabled testing.",
            ),
            _check_at_least(
                "lock_suppressed_count",
                summary.lock_suppressed_count,
                min_social_lock_count,
                "Lock-disabled attack tests should suppress, not execute, workstation locking.",
            ),
            _check_equal(
                "locked_count",
                summary.locked_count,
                0,
                "Lock-disabled attack tests must not transition to LOCKED.",
            ),
        ]
    elif scenario == "spoof":
        checks = [
            _check_equal(
                "entered_active",
                summary.entered_active,
                False,
                "Spoof/replay runs must never be accepted as the enrolled owner.",
            ),
            _check_at_most(
                "active_hold_seconds",
                summary.active_hold_seconds,
                max_attack_active_seconds,
                "Spoof/replay runs should not hold ACTIVE.",
            ),
            _check_at_least(
                "scanning_liveness_count",
                summary.scanning_liveness.count,
                min_attack_evidence_frames,
                "Spoof/replay assessment needs enough scanning-frame liveness evidence.",
            ),
            _check_at_most(
                "spoof_liveness_median",
                summary.scanning_liveness.median,
                max_spoof_liveness_median,
                "Spoof/replay liveness median should stay below the calibrated fail-closed band.",
            ),
            _check_at_most(
                "spoof_liveness_max",
                summary.scanning_liveness.max,
                max_spoof_liveness_max,
                "Spoof/replay liveness spikes should remain below the owner-acceptance band.",
            ),
            _check_equal(
                "social_lock_count",
                summary.social_lock_count,
                0,
                "Spoof/replay should be rejected by liveness, not classified as a live stranger.",
            ),
            _check_equal(
                "locked_count",
                summary.locked_count,
                0,
                "Lock-disabled spoof tests must not transition to LOCKED.",
            ),
        ]
    else:
        raise ValueError(f"unknown attack assessment scenario: {scenario!r}")

    return RunAssessment(ok=all(check.ok for check in checks), checks=checks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a MajestyGuard daemon log.")
    parser.add_argument("log_path", nargs="+")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--assess", action="store_true", help="Also emit hardening pass/fail gates.")
    parser.add_argument(
        "--attack-assess",
        choices=("stranger", "spoof"),
        help="Emit pass/fail gates for a controlled attack scenario.",
    )
    parser.add_argument("--min-runs", type=int, default=1)
    parser.add_argument("--min-active-seconds", type=float, default=60.0)
    parser.add_argument("--min-active-score-median", type=float, default=0.70)
    parser.add_argument("--min-active-presence-median", type=float, default=0.70)
    parser.add_argument("--min-active-liveness-median", type=float, default=0.70)
    parser.add_argument("--max-active-no-face-frames", type=int, default=10)
    parser.add_argument("--max-definite-stranger-frames", type=int, default=0)
    parser.add_argument("--min-attack-evidence-frames", type=int, default=3)
    parser.add_argument("--min-social-lock-count", type=int, default=1)
    parser.add_argument("--max-spoof-liveness-median", type=float, default=0.60)
    parser.add_argument("--max-spoof-liveness-max", type=float, default=0.70)
    parser.add_argument("--max-attack-active-seconds", type=float, default=0.0)
    args = parser.parse_args()
    if args.assess and args.attack_assess:
        parser.error("choose either --assess or --attack-assess, not both")
    if args.attack_assess and len(args.log_path) != 1:
        parser.error("--attack-assess expects exactly one log path")

    summaries = [summarize_file(path) for path in args.log_path]
    summary = summaries[0]
    data = asdict(summary) if len(summaries) == 1 else {"runs": [asdict(item) for item in summaries]}
    assessment = None
    if args.assess:
        if len(summaries) > 1 or args.min_runs > 1:
            assessment = assess_repeated_owner_runs(
                summaries,
                min_runs=max(1, args.min_runs),
                min_active_seconds=args.min_active_seconds,
                min_active_score_median=args.min_active_score_median,
                min_active_presence_median=args.min_active_presence_median,
                min_active_liveness_median=args.min_active_liveness_median,
                max_active_no_face_frames=max(0, args.max_active_no_face_frames),
                max_definite_stranger_frames=max(0, args.max_definite_stranger_frames),
            )
            data = {
                "runs": [asdict(item) for item in summaries],
                "repeated_assessment": asdict(assessment),
            }
        else:
            assessment = assess_summary(
                summary,
                min_active_seconds=args.min_active_seconds,
                min_active_score_median=args.min_active_score_median,
                min_active_presence_median=args.min_active_presence_median,
                min_active_liveness_median=args.min_active_liveness_median,
                max_active_no_face_frames=max(0, args.max_active_no_face_frames),
                max_definite_stranger_frames=max(0, args.max_definite_stranger_frames),
            )
            data["assessment"] = asdict(assessment)
    elif args.attack_assess:
        assessment = assess_attack_summary(
            summary,
            scenario=args.attack_assess,
            min_attack_evidence_frames=max(1, args.min_attack_evidence_frames),
            min_social_lock_count=max(1, args.min_social_lock_count),
            max_spoof_liveness_median=args.max_spoof_liveness_median,
            max_spoof_liveness_max=args.max_spoof_liveness_max,
            max_attack_active_seconds=max(0.0, args.max_attack_active_seconds),
        )
        data["attack_assessment"] = asdict(assessment)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0 if assessment is None or assessment.ok else 1

    print("MajestyGuard daemon run summary")
    print(f"  first_timestamp: {summary.first_timestamp}")
    print(f"  last_timestamp: {summary.last_timestamp}")
    print(f"  duration_seconds: {summary.duration_seconds:.3f}")
    print(f"  entered_active: {summary.entered_active}")
    print(f"  active_hold_seconds: {summary.active_hold_seconds:.3f}")
    print(f"  state_transitions: {summary.state_transitions}")
    print(f"  social_lock_count: {summary.social_lock_count}")
    print(f"  locked_count: {summary.locked_count}")
    print(f"  lock_suppressed_count: {summary.lock_suppressed_count}")
    print(f"  low_score_reasons: {summary.low_score_reasons}")
    print(f"  scanning_score: {summary.scanning_score}")
    print(f"  scanning_liveness: {summary.scanning_liveness}")
    print(f"  scanning_quality: {summary.scanning_quality}")
    print(f"  scanning_smooth: {summary.scanning_smooth}")
    print(f"  scanning_presence: {summary.scanning_presence}")
    print(f"  scanning_no_face_frame_count: {summary.scanning_no_face_frame_count}")
    print(f"  active_score: {summary.active_score}")
    print(f"  active_liveness: {summary.active_liveness}")
    print(f"  active_quality: {summary.active_quality}")
    print(f"  active_no_face_frame_count: {summary.active_no_face_frame_count}")
    print(f"  active_smooth: {summary.active_smooth}")
    print(f"  active_presence: {summary.active_presence}")
    print(f"  active_raw_faces: {summary.active_raw_faces}")
    print(f"  active_face_height: {summary.active_face_height}")
    print(f"  active_center_offset: {summary.active_center_offset}")
    print(f"  active_sticky_iou: {summary.active_sticky_iou}")
    print(f"  active_kalman_iou: {summary.active_kalman_iou}")
    print(f"  active_inference_ms: {summary.active_inference_ms}")
    print(f"  selection_reasons: {summary.selection_reasons}")
    print(f"  template_hits: {summary.template_hits}")
    print(f"  low_score_score: {summary.low_score_score}")
    print(f"  low_score_smooth: {summary.low_score_smooth}")
    print(f"  low_score_liveness: {summary.low_score_liveness}")
    print(f"  low_score_quality: {summary.low_score_quality}")
    print(f"  low_score_face_height: {summary.low_score_face_height}")
    print(f"  low_score_center_offset: {summary.low_score_center_offset}")
    print(f"  active_continuity_hold_count: {summary.active_continuity_hold_count}")
    print(f"  active_continuity_reasons: {summary.active_continuity_reasons}")
    print(f"  active_continuity_score: {summary.active_continuity_score}")
    print(f"  active_continuity_smooth: {summary.active_continuity_smooth}")
    print(f"  active_continuity_liveness: {summary.active_continuity_liveness}")
    print(f"  active_continuity_quality: {summary.active_continuity_quality}")
    print(f"  active_continuity_face_height: {summary.active_continuity_face_height}")
    print(f"  active_continuity_center_offset: {summary.active_continuity_center_offset}")
    print(f"  definite_stranger_count: {summary.definite_stranger_count}")
    print(f"  definite_stranger_reasons: {summary.definite_stranger_reasons}")
    print(f"  definite_stranger_score: {summary.definite_stranger_score}")
    print(f"  definite_stranger_smooth: {summary.definite_stranger_smooth}")
    print(f"  definite_stranger_liveness: {summary.definite_stranger_liveness}")
    print(f"  definite_stranger_quality: {summary.definite_stranger_quality}")
    print(f"  definite_stranger_face_height: {summary.definite_stranger_face_height}")
    print(f"  definite_stranger_center_offset: {summary.definite_stranger_center_offset}")
    print(f"  definite_stranger_sticky_iou: {summary.definite_stranger_sticky_iou}")
    print(f"  definite_stranger_kalman_iou: {summary.definite_stranger_kalman_iou}")
    if assessment is not None:
        print("  assessment:")
        print(f"    ok: {assessment.ok}")
        for check in assessment.checks:
            status = "PASS" if check.ok else "FAIL"
            print(
                f"    {status} {check.name}: observed={check.observed} "
                f"required={check.required} - {check.message}"
            )
    return 0 if assessment is None or assessment.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
