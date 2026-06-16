import json
import numpy as np

import enroll_v2
from enroll_v2 import (
    EnrollmentSample,
    _angles_for_profile,
    _default_max_templates_for_profile,
    _min_selected_per_angle_for_budget,
    _select_gallery,
    _undercovered_selected_angles,
)


def _sample(vector, idx, quality=0.90):
    emb = np.array(vector, dtype=np.float32)
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return EnrollmentSample(
        embedding=emb,
        angle=f"angle-{idx}",
        angle_index=idx,
        sample_index=1,
        det_score=0.90,
        height_frac=0.50,
        quality=quality,
    )


def test_select_gallery_rejects_identity_outlier():
    samples = [
        _sample([1.0, 0.0, 0.0], 1),
        _sample([0.99, 0.04, 0.0], 2),
        _sample([0.98, -0.02, 0.0], 3),
        _sample([0.97, 0.03, 0.0], 4),
        _sample([0.96, -0.03, 0.0], 5),
        _sample([0.0, 1.0, 0.0], 6),
    ]

    selected = _select_gallery(samples, max_templates=6, min_cohesion=0.70, duplicate_similarity=0.999)

    assert len(selected) == 5
    assert all(sample.angle_index != 6 for sample in selected)


def test_select_gallery_prunes_near_duplicates_but_keeps_diverse_views():
    samples = [
        _sample([1.0, 0.0, 0.0], 1, quality=0.95),
        _sample([0.999, 0.001, 0.0], 2, quality=0.94),
        _sample([0.80, 0.60, 0.0], 3, quality=0.90),
        _sample([0.60, 0.80, 0.0], 4, quality=0.89),
    ]

    selected = _select_gallery(samples, max_templates=3, min_cohesion=0.0, duplicate_similarity=0.995)
    selected_ids = {sample.angle_index for sample in selected}

    assert len(selected) == 3
    assert len({1, 2} & selected_ids) == 1
    assert 3 in selected_ids
    assert 4 in selected_ids


def test_select_gallery_reserves_valid_template_for_each_pose_when_budget_allows():
    samples = [
        _sample([1.0, 0.0, 0.0], 1, quality=0.99),
        _sample([0.995, 0.10, 0.0], 1, quality=0.98),
        _sample([0.99, -0.12, 0.0], 1, quality=0.97),
        _sample([0.90, 0.44, 0.0], 2, quality=0.96),
        _sample([0.88, 0.47, 0.0], 2, quality=0.95),
        _sample([0.86, 0.51, 0.0], 2, quality=0.94),
        _sample([0.44, 0.90, 0.0], 3, quality=0.93),
        _sample([0.47, 0.88, 0.0], 3, quality=0.92),
        _sample([0.51, 0.86, 0.0], 3, quality=0.91),
        _sample([0.70, 0.70, 0.14], 4, quality=0.90),
        _sample([0.68, 0.72, 0.14], 4, quality=0.89),
        _sample([0.72, 0.68, 0.14], 4, quality=0.88),
        _sample([0.995, -0.10, 0.0], 5, quality=0.45),
        _sample([0.992, -0.12, 0.0], 5, quality=0.44),
        _sample([0.989, -0.14, 0.0], 5, quality=0.43),
    ]

    selected = _select_gallery(samples, max_templates=10, min_cohesion=0.0, duplicate_similarity=0.999)
    counts = {}
    for sample in selected:
        counts[sample.angle_index] = counts.get(sample.angle_index, 0) + 1

    assert counts == {1: 2, 2: 2, 3: 2, 4: 2, 5: 2}


def test_enrollment_undercoverage_gate_detects_pruned_pose_gap():
    angles = ["front", "left", "right"]
    selected = [
        _sample([1.0, 0.0, 0.0], 1),
        _sample([0.99, 0.1, 0.0], 1),
        _sample([0.8, 0.6, 0.0], 2),
        _sample([0.75, 0.65, 0.0], 2),
        _sample([0.7, 0.7, 0.0], 3),
    ]
    for sample, angle in zip(selected, ["front", "front", "left", "left", "right"]):
        sample.angle = angle

    assert _undercovered_selected_angles(selected, angles, min_selected_per_angle=2) == {"right": 1}


def test_min_selected_per_angle_relaxes_when_template_budget_is_too_small():
    assert _min_selected_per_angle_for_budget(angle_count=12, max_templates=40) == 2
    assert _min_selected_per_angle_for_budget(angle_count=12, max_templates=12) == 1


def test_real_life_profile_covers_night_lying_close_face_poses():
    angles = _angles_for_profile("real-life")

    assert len(angles) >= 16
    assert any("Night lying down" in angle for angle in angles)
    assert any("head tilted left" in angle for angle in angles)
    assert any("head tilted right" in angle for angle in angles)
    assert any("close relaxed face" in angle for angle in angles)
    assert any("Leaning back" in angle for angle in angles)
    assert _default_max_templates_for_profile("real-life", len(angles), samples_per_angle=4) >= len(angles) * 2


def test_write_metadata_records_declared_pose_count(tmp_path, monkeypatch):
    meta_path = tmp_path / "embeddings_v2_meta.json"
    monkeypatch.setattr(enroll_v2, "OUT_META_FILE", meta_path)
    samples = [_sample([1.0, 0.0, 0.0], 1), _sample([0.0, 1.0, 0.0], 2)]
    samples[0].angle = "front"
    samples[1].angle = "left"

    enroll_v2._write_metadata(
        profile="test",
        angles=["front", "left"],
        samples_per_angle=1,
        all_samples=samples,
        selected=samples,
        max_templates=2,
        min_cohesion=0.45,
        duplicate_similarity=0.985,
        min_quality=0.62,
        min_selected_per_angle=1,
    )

    payload = json.loads(meta_path.read_text(encoding="utf-8"))

    assert payload["min_angle_count"] == 2


def test_enrollment_timeout_defaults_from_mg_max_seconds():
    assert enroll_v2._env_max_seconds({"MG_MAX_SECONDS": "180"}) == 180.0
    assert enroll_v2._env_max_seconds({"MG_MAX_SECONDS": "bad"}) == 0.0
    assert enroll_v2._env_max_seconds({"MG_MAX_SECONDS": "-5"}) == 0.0
    assert enroll_v2._env_max_seconds({}) == 0.0


def test_enrollment_deadline_helpers_treat_zero_as_unbounded():
    assert enroll_v2._deadline_from_max_seconds(0.0, now=100.0) is None
    assert enroll_v2._deadline_from_max_seconds(-1.0, now=100.0) is None
    assert enroll_v2._deadline_from_max_seconds(30.0, now=100.0) == 130.0

    assert not enroll_v2._enrollment_deadline_expired(None, now=999.0)
    assert not enroll_v2._enrollment_deadline_expired(130.0, now=129.9)
    assert enroll_v2._enrollment_deadline_expired(130.0, now=130.0)
