import numpy as np
import pytest

from check_enrollment import analyze_embeddings


def _unit(v):
    arr = np.array(v, dtype=np.float32)
    return arr / (np.linalg.norm(arr) + 1e-8)


def test_analyze_embeddings_reports_pairwise_similarity_stats():
    embeddings = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.8, 0.6, 0.0]),
        _unit([0.8, 0.0, 0.6]),
    ])

    report = analyze_embeddings(embeddings)

    assert report.count == 3
    assert report.dimension == 3
    assert report.pair_count == 3
    assert report.min_pairwise == pytest.approx(0.64)
    assert report.max_pairwise == pytest.approx(0.80)
    assert report.mean_pairwise == pytest.approx(0.7466666666666667)
    assert not report.too_similar
    assert not report.has_outlier
    assert report.phase1_ok
    assert report.outlier_indices == []


def test_analyze_embeddings_warns_when_gallery_is_too_similar():
    embeddings = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.999, 0.040, 0.0]),
        _unit([0.998, 0.063, 0.0]),
    ])

    report = analyze_embeddings(embeddings)

    assert report.mean_pairwise > 0.97
    assert report.too_similar


def test_analyze_embeddings_warns_for_identity_outlier():
    embeddings = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.95, 0.31, 0.0]),
        _unit([0.0, 1.0, 0.0]),
    ])

    report = analyze_embeddings(embeddings)

    assert report.min_pairwise < 0.40
    assert report.has_outlier
    assert report.outlier_indices == [2]


def test_analyze_embeddings_requires_phase1_mean_similarity_band():
    too_diverse = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.45, 0.89, 0.0]),
        _unit([0.45, 0.0, 0.89]),
    ])
    too_tight = np.stack([
        _unit([1.0, 0.0, 0.0]),
        _unit([0.98, 0.20, 0.0]),
        _unit([0.96, 0.28, 0.0]),
    ])

    assert not analyze_embeddings(too_diverse).phase1_ok
    assert not analyze_embeddings(too_tight).phase1_ok
