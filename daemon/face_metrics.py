"""
MajestyGuard face-system metrics.

Scores are interpreted as "higher means accept as genuine." Labels use True
for genuine/owner/live samples and False for attack/impostor samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class ThresholdMetrics:
    threshold: float
    genuine_count: int
    attack_count: int
    failed_count: int
    false_accepts: int
    false_rejects: int
    apcer: float
    bpcer: float
    fter: float


@dataclass(frozen=True)
class EerResult:
    threshold: float
    eer: float
    apcer: float
    bpcer: float


def _as_arrays(labels: Iterable[bool], scores: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    label_arr = np.asarray(list(labels), dtype=bool)
    score_arr = np.asarray(list(scores), dtype=np.float64)
    if label_arr.shape[0] != score_arr.shape[0]:
        raise ValueError("labels and scores must have the same length")
    if label_arr.shape[0] == 0:
        raise ValueError("labels and scores must not be empty")
    return label_arr, score_arr


def confusion_at_threshold(labels: Iterable[bool], scores: Iterable[float], threshold: float) -> ThresholdMetrics:
    label_arr, score_arr = _as_arrays(labels, scores)
    finite = np.isfinite(score_arr)
    valid_labels = label_arr[finite]
    valid_scores = score_arr[finite]

    genuine = valid_labels
    attacks = ~valid_labels
    accepts = valid_scores >= float(threshold)

    false_accepts = int(np.sum(accepts & attacks))
    false_rejects = int(np.sum((~accepts) & genuine))
    genuine_count = int(np.sum(genuine))
    attack_count = int(np.sum(attacks))
    failed_count = int(np.sum(~finite))
    total_count = int(label_arr.shape[0])

    apcer = false_accepts / attack_count if attack_count else 0.0
    bpcer = false_rejects / genuine_count if genuine_count else 0.0
    fter = failed_count / total_count

    return ThresholdMetrics(
        threshold=float(threshold),
        genuine_count=genuine_count,
        attack_count=attack_count,
        failed_count=failed_count,
        false_accepts=false_accepts,
        false_rejects=false_rejects,
        apcer=float(apcer),
        bpcer=float(bpcer),
        fter=float(fter),
    )


def equal_error_rate(labels: Iterable[bool], scores: Iterable[float]) -> EerResult:
    label_arr, score_arr = _as_arrays(labels, scores)
    finite = np.isfinite(score_arr)
    if not np.any(finite):
        raise ValueError("at least one finite score is required")

    valid_scores = score_arr[finite]
    thresholds = np.unique(valid_scores)
    thresholds = np.concatenate((
        np.array([float(np.max(valid_scores)) + 1e-6]),
        thresholds[::-1],
        np.array([float(np.min(valid_scores)) - 1e-6]),
    ))

    best = None
    for threshold in thresholds:
        metrics = confusion_at_threshold(label_arr, score_arr, float(threshold))
        gap = abs(metrics.apcer - metrics.bpcer)
        rank = (gap, metrics.apcer + metrics.bpcer)
        if best is None or rank < best[0]:
            best = (rank, metrics)

    assert best is not None
    metrics = best[1]
    eer = (metrics.apcer + metrics.bpcer) / 2.0
    return EerResult(
        threshold=metrics.threshold,
        eer=float(eer),
        apcer=metrics.apcer,
        bpcer=metrics.bpcer,
    )
