"""
Offline Phase 1 acceptance gate for MajestyGuard v2.

This module only parses diagnostic text and explicit counters. It does not open
the camera, start services, register a Credential Provider, or change machine
state.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from diagnostic_common import posture_label as _posture_label


@dataclass(frozen=True)
class RecognitionMetrics:
    posture: str
    max_score: float
    median_score: float
    no_embedding_frames: int


@dataclass(frozen=True)
class LivenessMetrics:
    p10: float
    p50: float
    onnx_mean: float
    skipped_quality_percent: float


@dataclass(frozen=True)
class PhaseGateInputs:
    selected_templates: int
    mean_pairwise: float
    min_pairwise: float
    outlier_count: int
    recognition_runs: list[RecognitionMetrics] = field(default_factory=list)
    liveness: LivenessMetrics | None = None
    daemon_entered_active: bool = False
    daemon_active_hold_seconds: float = 0.0
    daemon_social_lock_count: int = 0
    daemon_run_present: bool = False


@dataclass(frozen=True)
class PhaseGateResult:
    status: str
    failed_checks: list[str]
    caution_checks: list[str]
    checks: dict[str, bool]


def _check_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_") or "unspecified"


def _required_float(text: str, pattern: str, name: str) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError(f"missing {name}")
    return float(match.group(1))


def _required_int(text: str, pattern: str, name: str) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError(f"missing {name}")
    return int(match.group(1))


def _read_log_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def parse_recognition_text(text: str) -> RecognitionMetrics:
    posture_match = re.search(r"^\s*posture\s*:\s*(.+?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    posture = _posture_label(posture_match.group(1) if posture_match else None)
    return RecognitionMetrics(
        posture=posture,
        max_score=_required_float(text, r"^\s*max\s*:\s*([0-9.]+)\s*$", "recognition max"),
        median_score=_required_float(text, r"^\s*median\s*:\s*([0-9.]+)\s*$", "recognition median"),
        no_embedding_frames=_required_int(
            text,
            r"^\s*no_embedding_frames\s*:\s*(\d+)\s*$",
            "recognition no_embedding_frames",
        ),
    )


def parse_liveness_text(text: str) -> LivenessMetrics:
    total_frames_match = re.search(r"^\s*total_frames\s*:\s*(\d+)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    skipped_match = re.search(
        r"^\s*skipped_quality_frames\s*:\s*(\d+)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    total_frames = int(total_frames_match.group(1)) if total_frames_match else 0
    skipped_frames = int(skipped_match.group(1)) if skipped_match else 0
    skipped_percent = (skipped_frames / total_frames * 100.0) if total_frames > 0 else 0.0

    smoothed_line = re.search(r"^\s*smoothed\b(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    onnx_line = re.search(r"^\s*onnx\b(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if not smoothed_line:
        raise ValueError("missing smoothed liveness line")
    if not onnx_line:
        raise ValueError("missing onnx liveness line")
    smoothed = smoothed_line.group(1)
    onnx = onnx_line.group(1)

    return LivenessMetrics(
        p10=_required_float(smoothed, r"p10\s*=\s*([0-9.]+)", "liveness p10"),
        p50=_required_float(smoothed, r"median\s*=\s*([0-9.]+)", "liveness median"),
        onnx_mean=_required_float(onnx, r"mean\s*=\s*([0-9.]+)", "onnx mean"),
        skipped_quality_percent=skipped_percent,
    )


def evaluate_phase1(inputs: PhaseGateInputs) -> PhaseGateResult:
    checks: dict[str, bool] = {
        "selected_templates": 40 <= inputs.selected_templates <= 60,
        "mean_pairwise": 0.65 <= inputs.mean_pairwise <= 0.85,
        "min_pairwise": inputs.min_pairwise > 0.45,
        "outlier_count": inputs.outlier_count == 0,
        "recognition_run_count": len(inputs.recognition_runs) >= 3,
        "liveness_present": inputs.liveness is not None,
    }
    caution = [
        name
        for name in ("recognition_run_count", "liveness_present")
        if not checks[name]
    ]

    for run in inputs.recognition_runs:
        label = _check_label(run.posture)
        checks[f"recognition_{label}_max"] = run.max_score >= 0.85
        checks[f"recognition_{label}_median"] = run.median_score >= 0.80
        checks[f"recognition_{label}_no_embedding"] = run.no_embedding_frames == 0

    if inputs.liveness is not None:
        checks["liveness_p10"] = inputs.liveness.p10 >= 0.70
        checks["liveness_p50"] = inputs.liveness.p50 >= 0.78
        checks["liveness_onnx_mean"] = inputs.liveness.onnx_mean >= 0.90
        checks["liveness_skipped_quality"] = inputs.liveness.skipped_quality_percent <= 10.0

    daemon_run_present = (
        inputs.daemon_run_present
        or inputs.daemon_entered_active
        or inputs.daemon_active_hold_seconds > 0.0
        or inputs.daemon_social_lock_count > 0
    )
    checks["daemon_run_present"] = daemon_run_present
    if daemon_run_present:
        checks["daemon_entered_active"] = inputs.daemon_entered_active
        checks["daemon_active_hold_seconds"] = inputs.daemon_active_hold_seconds >= 60.0
        checks["daemon_social_lock_count"] = inputs.daemon_social_lock_count == 0
    else:
        caution.append("daemon_run_present")

    failed = [name for name, ok in checks.items() if not ok and name not in caution]
    status = "FAIL" if failed else ("CAUTION" if caution else "PASS")
    return PhaseGateResult(
        status=status,
        failed_checks=failed,
        caution_checks=caution,
        checks=checks,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate offline MajestyGuard Phase 1 evidence.")
    parser.add_argument("--recognition-output", type=Path, action="append", default=[])
    parser.add_argument("--liveness-output", type=Path)
    parser.add_argument("--selected-templates", type=int, required=True)
    parser.add_argument("--mean-pairwise", type=float, required=True)
    parser.add_argument("--min-pairwise", type=float, required=True)
    parser.add_argument("--outlier-count", type=int, default=0)
    parser.add_argument("--daemon-entered-active", action="store_true")
    parser.add_argument("--daemon-active-hold-seconds", type=float, default=0.0)
    parser.add_argument("--daemon-social-lock-count", type=int, default=0)
    parser.add_argument("--daemon-run-present", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    inputs = PhaseGateInputs(
        selected_templates=args.selected_templates,
        mean_pairwise=args.mean_pairwise,
        min_pairwise=args.min_pairwise,
        outlier_count=args.outlier_count,
        recognition_runs=[
            parse_recognition_text(_read_log_text(path))
            for path in args.recognition_output
        ],
        liveness=(
            parse_liveness_text(_read_log_text(args.liveness_output))
            if args.liveness_output
            else None
        ),
        daemon_entered_active=args.daemon_entered_active,
        daemon_active_hold_seconds=args.daemon_active_hold_seconds,
        daemon_social_lock_count=args.daemon_social_lock_count,
        daemon_run_present=args.daemon_run_present,
    )
    result = evaluate_phase1(inputs)
    if args.json:
        print(json.dumps({"inputs": asdict(inputs), "result": asdict(result)}, indent=2, sort_keys=True))
    else:
        print(f"Phase 1 gate: {result.status}")
        for name, ok in result.checks.items():
            if name in result.failed_checks:
                label = "FAIL"
            elif name in result.caution_checks:
                label = "CAUTION"
            else:
                label = "PASS" if ok else "FAIL"
            print(f"  {label} {name}")
    if result.status == "PASS":
        return 0
    return 2 if result.status == "CAUTION" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
