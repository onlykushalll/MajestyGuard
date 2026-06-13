"""
Offline enrollment-gallery diversity check.

This tool reads embeddings_v2.npy and checks whether the selected owner
templates are diverse enough for robust recognition without including obvious
identity outliers. It does not open the camera and does not write frames.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np


DEFAULT_EMBEDDINGS_PATH = (
    Path(os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData")))
    / "MajestyGuard"
    / "embeddings_v2.npy"
)


@dataclass(frozen=True)
class EnrollmentReport:
    count: int
    dimension: int
    pair_count: int
    mean_pairwise: float
    min_pairwise: float
    max_pairwise: float
    too_similar: bool
    has_outlier: bool
    phase1_ok: bool
    outlier_indices: list[int]
    pairwise_similarities: np.ndarray


def analyze_embeddings(
    embeddings: np.ndarray,
    *,
    too_similar_threshold: float = 0.97,
    outlier_threshold: float = 0.40,
    phase1_mean_min: float = 0.65,
    phase1_mean_max: float = 0.85,
    phase1_min_pairwise: float = 0.45,
) -> EnrollmentReport:
    arr = np.asarray(embeddings, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"expected a 2D embedding matrix, got shape {arr.shape}")
    if arr.shape[0] < 2:
        raise ValueError("need at least two embeddings for pairwise analysis")
    if not np.all(np.isfinite(arr)):
        raise ValueError("embedding matrix contains non-finite values")

    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    if np.any(norms <= 1e-8):
        raise ValueError("embedding matrix contains zero-length vectors")
    normalized = arr / norms

    similarity = normalized @ normalized.T
    upper = np.triu_indices(arr.shape[0], k=1)
    pairwise = similarity[upper].astype(np.float64)

    mean_pairwise = float(np.mean(pairwise))
    min_pairwise = float(np.min(pairwise))
    max_pairwise = float(np.max(pairwise))
    nearest = similarity.copy()
    np.fill_diagonal(nearest, -np.inf)
    nearest_scores = np.max(nearest, axis=1)
    outlier_indices = [
        int(index)
        for index, score in enumerate(nearest_scores.tolist())
        if float(score) < outlier_threshold
    ]
    phase1_ok = (
        phase1_mean_min <= mean_pairwise <= phase1_mean_max
        and min_pairwise > phase1_min_pairwise
        and not outlier_indices
    )
    return EnrollmentReport(
        count=int(arr.shape[0]),
        dimension=int(arr.shape[1]),
        pair_count=int(pairwise.size),
        mean_pairwise=mean_pairwise,
        min_pairwise=min_pairwise,
        max_pairwise=max_pairwise,
        too_similar=mean_pairwise > too_similar_threshold,
        has_outlier=bool(outlier_indices),
        phase1_ok=phase1_ok,
        outlier_indices=outlier_indices,
        pairwise_similarities=pairwise,
    )


def _write_histogram(pairwise: np.ndarray, output_path: Path) -> bool:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 4))
    plt.hist(pairwise, bins=24, range=(-0.1, 1.0), color="#2f6fed", edgecolor="#102040")
    plt.title("MajestyGuard Enrollment Pairwise Cosine Similarity")
    plt.xlabel("Cosine similarity")
    plt.ylabel("Pair count")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    return True


def _print_report(report: EnrollmentReport, path: Path) -> None:
    print("MajestyGuard enrollment diversity check")
    print(f"  file: {path}")
    print(f"  shape: ({report.count}, {report.dimension})")
    print(f"  pair_count: {report.pair_count}")
    print(f"  mean_pairwise_sim: {report.mean_pairwise:.3f}")
    print(f"  min_pairwise_sim: {report.min_pairwise:.3f}")
    print(f"  max_pairwise_sim: {report.max_pairwise:.3f}")
    print(f"  phase1_ok: {report.phase1_ok}")
    print(f"  outlier_indices: {report.outlier_indices}")
    if report.too_similar:
        print("WARNING: embeddings too similar, re-enroll with more variation")
    else:
        print("OK: gallery has useful pose/quality diversity")
    if report.has_outlier:
        print("WARNING: outlier embedding, may hurt recognition consistency")
    else:
        print("OK: no obvious identity outlier below cosine 0.40")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_EMBEDDINGS_PATH)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument(
        "--histogram",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData")))
        / "MajestyGuard"
        / "enrollment_pairwise_hist.png",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: embedding file not found: {args.path}")
        return 2

    report = analyze_embeddings(np.load(str(args.path)))
    _print_report(report, args.path)

    if not args.no_plot:
        if _write_histogram(report.pairwise_similarities, args.histogram):
            print(f"  histogram: {args.histogram}")
        else:
            print("  histogram: matplotlib unavailable, skipped")

    return 0 if report.phase1_ok and not report.too_similar else 1


if __name__ == "__main__":
    raise SystemExit(main())
