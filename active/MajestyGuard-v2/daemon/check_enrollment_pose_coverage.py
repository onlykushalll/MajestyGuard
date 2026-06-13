"""
Offline pose-coverage audit for the v2 enrollment gallery.

Reads embeddings_v2_meta.json only. It does not open the camera, save frames,
start IPC, lock the workstation, or touch login integration.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np


DEFAULT_META_PATH = (
    Path(os.environ.get("LOCALAPPDATA", "C:/tmp"))
    / "MajestyGuard"
    / "embeddings_v2_meta.json"
)
DEFAULT_EMBEDDINGS_PATH = DEFAULT_META_PATH.with_name("embeddings_v2.npy")

EXPECTED_SELECTION_POLICY = "pose_coverage_then_quality_diversity"
MIN_METADATA_VERSION = 5
PROFILE_MIN_ANGLE_COUNTS = {
    "quick": 6,
    "robust": 12,
    "real-life": 19,
}


def analyze_pose_coverage(
    metadata: dict,
    min_selected_per_angle: int | None = None,
    min_quality: float | None = None,
    min_cohesion: float | None = None,
    min_angle_count: int | None = None,
    expected_selection_policy: str = EXPECTED_SELECTION_POLICY,
    min_metadata_version: int = MIN_METADATA_VERSION,
) -> dict:
    angles = list(metadata.get("angles") or [])
    selected = list(metadata.get("selected_samples") or [])
    all_samples = list(metadata.get("all_samples") or [])
    profile = str(metadata.get("profile") or "").lower()
    if min_quality is None:
        min_quality = float(metadata.get("min_quality", 0.62))
    if min_cohesion is None:
        min_cohesion = float(metadata.get("min_cohesion", 0.45))
    if min_selected_per_angle is None:
        min_selected_per_angle = int(metadata.get("min_selected_per_angle") or 2)
    min_selected_per_angle = max(1, int(min_selected_per_angle))
    if min_angle_count is None:
        profile_min_angle_count = PROFILE_MIN_ANGLE_COUNTS.get(profile, 1)
        min_angle_count = max(int(metadata.get("min_angle_count") or 1), profile_min_angle_count)
    min_angle_count = max(1, int(min_angle_count))

    selected_counts = {angle: 0 for angle in angles}
    captured_counts = {angle: 0 for angle in angles}
    unknown_captured_angles: dict[str, int] = {}
    unknown_selected_angles: dict[str, int] = {}

    for sample in all_samples:
        angle = sample.get("angle")
        if angle in captured_counts:
            captured_counts[angle] += 1
        else:
            _increment(unknown_captured_angles, str(angle or "unknown"))

    for sample in selected:
        angle = sample.get("angle")
        if angle in selected_counts:
            selected_counts[angle] += 1
        else:
            _increment(unknown_selected_angles, str(angle or "unknown"))

    undercovered = {
        angle: count
        for angle, count in selected_counts.items()
        if captured_counts.get(angle, 0) > 0 and count < min_selected_per_angle
    }
    missing = [angle for angle, count in captured_counts.items() if count == 0]
    low_quality_selected = [
        _sample_label(sample)
        for sample in selected
        if sample.get("quality") is not None and float(sample["quality"]) < min_quality
    ]
    weak_cohesion_selected = [
        _sample_label(sample)
        for sample in selected
        if sample.get("cohesion") is not None and float(sample["cohesion"]) < min_cohesion
    ]
    policy = metadata.get("selection_policy", "unknown")
    version = int(metadata.get("version") or 0)
    stale_policy = bool(expected_selection_policy and policy != expected_selection_policy)
    stale_version = version < min_metadata_version
    captured_count = metadata.get("captured_count")
    selected_count = metadata.get("selected_count")
    captured_count_mismatch = captured_count is not None and int(captured_count) != len(all_samples)
    selected_count_mismatch = selected_count is not None and int(selected_count) != len(selected)
    too_few_angles = len(angles) < min_angle_count

    return {
        "version": version,
        "profile": metadata.get("profile"),
        "captured_count": captured_count,
        "selected_count": selected_count,
        "selection_policy": policy,
        "angle_count": len(angles),
        "min_angle_count": min_angle_count,
        "min_selected_per_angle": min_selected_per_angle,
        "min_metadata_version": min_metadata_version,
        "min_quality": min_quality,
        "min_cohesion": min_cohesion,
        "captured_counts": captured_counts,
        "selected_counts": selected_counts,
        "unknown_captured_angles": unknown_captured_angles,
        "unknown_selected_angles": unknown_selected_angles,
        "undercovered": undercovered,
        "missing": missing,
        "low_quality_selected": low_quality_selected,
        "weak_cohesion_selected": weak_cohesion_selected,
        "captured_count_mismatch": captured_count_mismatch,
        "selected_count_mismatch": selected_count_mismatch,
        "too_few_angles": too_few_angles,
        "stale_policy": stale_policy,
        "stale_version": stale_version,
        "ok": (
            not undercovered
            and not missing
            and not unknown_captured_angles
            and not unknown_selected_angles
            and not low_quality_selected
            and not weak_cohesion_selected
            and not captured_count_mismatch
            and not selected_count_mismatch
            and not too_few_angles
            and not stale_policy
            and not stale_version
        ),
    }


def analyze_embedding_matrix(
    metadata: dict,
    matrix: np.ndarray,
    *,
    expected_width: int = 512,
    norm_tolerance: float = 0.02,
) -> dict:
    arr = np.asarray(matrix)
    shape = list(arr.shape)
    rank_ok = arr.ndim == 2
    width_ok = bool(rank_ok and arr.shape[1] == expected_width)
    finite = bool(np.isfinite(arr).all())
    selected_samples = list(metadata.get("selected_samples") or [])
    selected_count = metadata.get("selected_count")
    expected_count = int(selected_count) if selected_count is not None else len(selected_samples)
    count_matches_selected = bool(rank_ok and arr.shape[0] == expected_count)
    norms = np.linalg.norm(arr, axis=1) if rank_ok and arr.shape[0] > 0 else np.array([], dtype=np.float32)
    normalized = bool(finite and norms.size > 0 and np.all(np.abs(norms - 1.0) <= norm_tolerance))

    return {
        "shape": shape,
        "expected_width": width_ok,
        "expected_width_value": expected_width,
        "expected_count": expected_count,
        "count_matches_selected": count_matches_selected,
        "finite": finite,
        "normalized": normalized,
        "norm_min": float(np.min(norms)) if norms.size else None,
        "norm_max": float(np.max(norms)) if norms.size else None,
        "ok": bool(rank_ok and width_ok and count_matches_selected and finite and normalized),
    }


def analyze_enrollment_artifacts(
    metadata: dict,
    matrix: np.ndarray | None = None,
    *,
    min_selected_per_angle: int | None = None,
    min_quality: float | None = None,
    min_cohesion: float | None = None,
    min_angle_count: int | None = None,
    expected_selection_policy: str = EXPECTED_SELECTION_POLICY,
    min_metadata_version: int = MIN_METADATA_VERSION,
    expected_embedding_width: int = 512,
) -> dict:
    pose_report = analyze_pose_coverage(
        metadata,
        min_selected_per_angle=min_selected_per_angle,
        min_quality=min_quality,
        min_cohesion=min_cohesion,
        min_angle_count=min_angle_count,
        expected_selection_policy=expected_selection_policy,
        min_metadata_version=min_metadata_version,
    )
    embedding_report = None
    if matrix is not None:
        embedding_report = analyze_embedding_matrix(
            metadata,
            matrix,
            expected_width=expected_embedding_width,
        )

    return {
        **pose_report,
        "embedding_matrix": embedding_report,
        "ok": bool(pose_report["ok"] and (embedding_report is None or embedding_report["ok"])),
    }


def _sample_label(sample: dict) -> dict:
    return {
        "angle": sample.get("angle", "unknown"),
        "angle_index": sample.get("angle_index"),
        "sample_index": sample.get("sample_index"),
        "quality": sample.get("quality"),
        "cohesion": sample.get("cohesion"),
    }


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=DEFAULT_META_PATH)
    parser.add_argument("--embeddings", type=Path, default=DEFAULT_EMBEDDINGS_PATH)
    parser.add_argument("--skip-embedding-check", action="store_true")
    parser.add_argument("--min-selected-per-angle", type=int, default=None)
    parser.add_argument("--min-quality", type=float, default=None)
    parser.add_argument("--min-cohesion", type=float, default=None)
    parser.add_argument("--min-angle-count", type=int, default=None)
    parser.add_argument("--min-metadata-version", type=int, default=MIN_METADATA_VERSION)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    matrix = None
    if not args.skip_embedding_check:
        matrix = np.load(args.embeddings)
    report = analyze_enrollment_artifacts(
        metadata,
        matrix,
        min_selected_per_angle=args.min_selected_per_angle,
        min_quality=args.min_quality,
        min_cohesion=args.min_cohesion,
        min_angle_count=args.min_angle_count,
        min_metadata_version=max(1, args.min_metadata_version),
    )

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    print("MajestyGuard enrollment pose coverage")
    print(f"  file: {args.metadata}")
    if not args.skip_embedding_check:
        print(f"  embeddings: {args.embeddings}")
    print(f"  profile: {report['profile']}")
    print(f"  version: {report['version']}")
    print(f"  selection_policy: {report['selection_policy']}")
    print(f"  captured_count: {report['captured_count']}")
    print(f"  selected_count: {report['selected_count']}")
    print(f"  angle_count: {report['angle_count']}")
    print(f"  min_angle_count: {report['min_angle_count']}")
    print(f"  min_selected_per_angle: {report['min_selected_per_angle']}")
    print(f"  min_quality: {report['min_quality']}")
    print(f"  min_cohesion: {report['min_cohesion']}")
    print("  selected_counts:")
    for angle, count in report["selected_counts"].items():
        print(f"    {count:2d}  {angle}")

    if report["stale_version"]:
        print(f"WARN: metadata version is older than {report['min_metadata_version']}")
    if report["stale_policy"]:
        print(f"WARN: selection policy is not {EXPECTED_SELECTION_POLICY}")
    if report["too_few_angles"]:
        print(f"WARN: declared pose count is below {report['min_angle_count']}")
    if report["captured_count_mismatch"]:
        print("WARN: captured_count does not match all_samples length")
    if report["selected_count_mismatch"]:
        print("WARN: selected_count does not match selected_samples length")
    if report["unknown_captured_angles"]:
        print("WARN: captured samples include unknown pose labels:")
        for angle, count in report["unknown_captured_angles"].items():
            print(f"  - {angle}: {count}")
    if report["unknown_selected_angles"]:
        print("WARN: selected samples include unknown pose labels:")
        for angle, count in report["unknown_selected_angles"].items():
            print(f"  - {angle}: {count}")
    if report["missing"]:
        print("WARN: missing captured poses:")
        for angle in report["missing"]:
            print(f"  - {angle}")
    if report["undercovered"]:
        print("WARN: undercovered selected poses:")
        for angle, count in report["undercovered"].items():
            print(f"  - {angle}: {count}")
    if report["low_quality_selected"]:
        print("WARN: low-quality selected templates:")
        for sample in report["low_quality_selected"]:
            print(f"  - {sample}")
    if report["weak_cohesion_selected"]:
        print("WARN: weak-cohesion selected templates:")
        for sample in report["weak_cohesion_selected"]:
            print(f"  - {sample}")
    embedding_report = report.get("embedding_matrix")
    if embedding_report is not None:
        print("  embedding_matrix:")
        print(f"    shape: {embedding_report['shape']}")
        print(f"    expected_count: {embedding_report['expected_count']}")
        print(f"    norm_min: {embedding_report['norm_min']}")
        print(f"    norm_max: {embedding_report['norm_max']}")
        if not embedding_report["expected_width"]:
            print(f"WARN: embedding width is not {embedding_report['expected_width_value']}")
        if not embedding_report["count_matches_selected"]:
            print("WARN: embedding row count does not match selected_count")
        if not embedding_report["finite"]:
            print("WARN: embedding matrix contains NaN or infinite values")
        if not embedding_report["normalized"]:
            print("WARN: embedding rows are not normalized")
    if report["ok"]:
        print("OK: selected gallery has balanced, current, high-quality pose coverage")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
