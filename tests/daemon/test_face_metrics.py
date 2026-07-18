import math

import pytest

from face_metrics import confusion_at_threshold, equal_error_rate


def test_confusion_at_threshold_reports_apcer_bpcer_and_fter():
    labels = [True, True, True, False, False, False, True]
    scores = [0.91, 0.82, 0.40, 0.20, 0.72, 0.10, math.nan]

    metrics = confusion_at_threshold(labels, scores, threshold=0.75)

    assert metrics.genuine_count == 3
    assert metrics.attack_count == 3
    assert metrics.failed_count == 1
    assert metrics.false_accepts == 0
    assert metrics.false_rejects == 1
    assert metrics.apcer == 0.0
    assert metrics.bpcer == pytest.approx(1 / 3)
    assert metrics.fter == pytest.approx(1 / 7)


def test_confusion_at_threshold_counts_false_accepts():
    labels = [True, False, False]
    scores = [0.90, 0.86, 0.20]

    metrics = confusion_at_threshold(labels, scores, threshold=0.80)

    assert metrics.false_accepts == 1
    assert metrics.false_rejects == 0
    assert metrics.apcer == pytest.approx(0.5)
    assert metrics.bpcer == 0.0


def test_equal_error_rate_chooses_balanced_operating_point():
    labels = [True, True, False, False]
    scores = [0.90, 0.60, 0.70, 0.20]

    result = equal_error_rate(labels, scores)

    assert result.threshold == pytest.approx(0.70)
    assert result.apcer == pytest.approx(0.5)
    assert result.bpcer == pytest.approx(0.5)
    assert result.eer == pytest.approx(0.5)


def test_metrics_reject_empty_or_mismatched_inputs():
    with pytest.raises(ValueError):
        confusion_at_threshold([], [], threshold=0.5)

    with pytest.raises(ValueError):
        equal_error_rate([True], [0.9, 0.1])
