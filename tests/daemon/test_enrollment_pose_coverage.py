import numpy as np

from check_enrollment_pose_coverage import analyze_enrollment_artifacts, analyze_pose_coverage, main


def test_pose_coverage_flags_undercovered_selected_angles():
    metadata = {
        "version": 4,
        "profile": "robust",
        "captured_count": 6,
        "selected_count": 4,
        "angles": ["front", "left", "right"],
        "all_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
        ],
        "selected_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"},
            {"angle": "right"},
        ],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2, min_angle_count=3)

    assert not report["ok"]
    assert report["undercovered"] == {"left": 1, "right": 1}
    assert report["missing"] == []


def test_pose_coverage_accepts_balanced_gallery():
    metadata = {
        "version": 5,
        "profile": "robust",
        "captured_count": 6,
        "selected_count": 6,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": ["front", "left", "right"],
        "all_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
        ],
        "selected_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
        ],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2, min_angle_count=3)

    assert report["ok"]
    assert report["undercovered"] == {}
    assert report["selection_policy"] == "pose_coverage_then_quality_diversity"


def test_pose_coverage_flags_stale_metadata_policy_and_version():
    metadata = {
        "version": 3,
        "profile": "robust",
        "captured_count": 2,
        "selected_count": 2,
        "angles": ["front"],
        "all_samples": [{"angle": "front"}, {"angle": "front"}],
        "selected_samples": [{"angle": "front"}, {"angle": "front"}],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2)

    assert not report["ok"]
    assert report["stale_version"]
    assert report["stale_policy"]


def test_pose_coverage_flags_low_quality_and_weak_cohesion_selected_templates():
    metadata = {
        "version": 5,
        "profile": "robust",
        "captured_count": 2,
        "selected_count": 2,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "min_quality": 0.62,
        "min_cohesion": 0.45,
        "angles": ["front"],
        "all_samples": [{"angle": "front"}, {"angle": "front"}],
        "selected_samples": [
            {"angle": "front", "angle_index": 1, "sample_index": 1, "quality": 0.50, "cohesion": 0.90},
            {"angle": "front", "angle_index": 1, "sample_index": 2, "quality": 0.90, "cohesion": 0.20},
        ],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2)

    assert not report["ok"]
    assert len(report["low_quality_selected"]) == 1
    assert len(report["weak_cohesion_selected"]) == 1


def test_pose_coverage_flags_too_few_robust_profile_angles_even_if_list_is_balanced():
    metadata = {
        "version": 5,
        "profile": "robust",
        "captured_count": 6,
        "selected_count": 6,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": ["front", "left", "right"],
        "all_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
        ],
        "selected_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
        ],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2)

    assert not report["ok"]
    assert report["angle_count"] == 3
    assert report["min_angle_count"] == 12
    assert report["too_few_angles"]


def test_pose_coverage_flags_too_few_real_life_profile_angles():
    metadata = {
        "version": 5,
        "profile": "real-life",
        "captured_count": 12,
        "selected_count": 12,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": [f"angle-{idx}" for idx in range(12)],
        "all_samples": [{"angle": f"angle-{idx}"} for idx in range(12)],
        "selected_samples": [{"angle": f"angle-{idx}"} for idx in range(12)],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=1)

    assert not report["ok"]
    assert report["min_angle_count"] == 19
    assert report["too_few_angles"]


def test_pose_coverage_flags_metadata_count_mismatches_and_unknown_angles():
    metadata = {
        "version": 5,
        "profile": "test",
        "captured_count": 99,
        "selected_count": 1,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": ["front", "left", "right"],
        "all_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
            {"angle": "mystery"},
        ],
        "selected_samples": [
            {"angle": "front"}, {"angle": "front"},
            {"angle": "left"}, {"angle": "left"},
            {"angle": "right"}, {"angle": "right"},
            {"angle": "mystery"},
        ],
    }

    report = analyze_pose_coverage(metadata, min_selected_per_angle=2, min_angle_count=3)

    assert not report["ok"]
    assert report["captured_count_mismatch"]
    assert report["selected_count_mismatch"]
    assert report["unknown_captured_angles"] == {"mystery": 1}
    assert report["unknown_selected_angles"] == {"mystery": 1}


def test_enrollment_artifact_audit_accepts_balanced_metadata_and_normalized_matrix():
    metadata = {
        "version": 5,
        "profile": "test",
        "captured_count": 2,
        "selected_count": 2,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": ["front", "left"],
        "all_samples": [{"angle": "front"}, {"angle": "left"}],
        "selected_samples": [{"angle": "front"}, {"angle": "left"}],
    }
    matrix = np.eye(2, 512, dtype=np.float32)

    report = analyze_enrollment_artifacts(
        metadata,
        matrix,
        min_selected_per_angle=1,
        min_angle_count=2,
    )

    assert report["ok"]
    assert report["embedding_matrix"]["shape"] == [2, 512]
    assert report["embedding_matrix"]["count_matches_selected"]
    assert report["embedding_matrix"]["finite"]
    assert report["embedding_matrix"]["normalized"]


def test_enrollment_artifact_audit_flags_bad_embedding_matrix():
    metadata = {
        "version": 5,
        "profile": "test",
        "captured_count": 2,
        "selected_count": 2,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "angles": ["front", "left"],
        "all_samples": [{"angle": "front"}, {"angle": "left"}],
        "selected_samples": [{"angle": "front"}, {"angle": "left"}],
    }
    matrix = np.array([[1.0, 0.0, 0.0], [np.nan, 2.0, 0.0]], dtype=np.float32)

    report = analyze_enrollment_artifacts(
        metadata,
        matrix,
        min_selected_per_angle=1,
        min_angle_count=2,
    )

    assert not report["ok"]
    assert report["embedding_matrix"]["shape"] == [2, 3]
    assert not report["embedding_matrix"]["expected_width"]
    assert not report["embedding_matrix"]["finite"]
    assert not report["embedding_matrix"]["normalized"]


def test_check_enrollment_pose_coverage_main_success(tmp_path, monkeypatch):
    import json
    import sys
    metadata = {
        "version": 5,
        "profile": "quick",
        "captured_count": 6,
        "selected_count": 6,
        "selection_policy": "pose_coverage_then_quality_diversity",
        "min_quality": 0.62,
        "min_cohesion": 0.45,
        "angles": ["angle1", "angle2", "angle3", "angle4", "angle5", "angle6"],
        "all_samples": [{"angle": f"angle{i}"} for i in range(1, 7)],
        "selected_samples": [{"angle": f"angle{i}"} for i in range(1, 7)],
    }
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps(metadata), encoding="utf-8")
    
    matrix = np.eye(6, 512, dtype=np.float32)
    matrix_file = tmp_path / "embeddings.npy"
    np.save(matrix_file, matrix)

    monkeypatch.setattr(sys, "argv", [
        "check_enrollment_pose_coverage.py",
        "--metadata", str(meta_file),
        "--embeddings", str(matrix_file),
        "--min-selected-per-angle", "1",
        "--min-angle-count", "6",
        "--json"
    ])

    # Should exit with 0 (success)
    assert main() == 0

