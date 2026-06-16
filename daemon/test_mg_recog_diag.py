import numpy as np

from mg_recog_diag import _default_target, _posture_label, _template_descriptor, _template_pose_report


def test_default_target_uses_daemon_recognition_threshold():
    assert _default_target({}) == 0.78
    assert _default_target({"MG_RECOGNITION_THRESHOLD": "0.82"}) == 0.82
    assert _default_target({"MG_RECOGNITION_THRESHOLD": "bad"}) == 0.78


def test_template_descriptor_maps_index_to_selected_pose_metadata():
    metadata = {
        "selected_samples": [
            {"angle": "front", "angle_index": 1, "sample_index": 2, "quality": 0.91, "cohesion": 0.77},
            {"angle": "night lying down", "angle_index": 13, "sample_index": 1, "quality": 0.88},
        ]
    }

    descriptor = _template_descriptor(1, metadata)

    assert descriptor["index"] == 1
    assert descriptor["angle"] == "night lying down"
    assert descriptor["angle_index"] == 13
    assert descriptor["sample_index"] == 1
    assert descriptor["quality"] == 0.88


def test_template_pose_report_prioritizes_winners_and_adds_score_stats():
    metadata = {
        "selected_samples": [
            {"angle": "front", "angle_index": 1, "sample_index": 1},
            {"angle": "night close face", "angle_index": 14, "sample_index": 1},
            {"angle": "side", "angle_index": 3, "sample_index": 1},
        ]
    }
    winner_counts = np.array([1, 4, 0], dtype=np.int32)
    template_scores = [
        [0.50, 0.52],
        [0.61, 0.70, 0.67],
        [],
    ]

    report = _template_pose_report(winner_counts, template_scores, metadata, limit=2)

    assert [row["index"] for row in report] == [1, 0]
    assert report[0]["angle"] == "night close face"
    assert report[0]["wins"] == 4
    assert report[0]["median"] == np.float32(0.67).item()
    assert report[0]["max"] == np.float32(0.70).item()


def test_posture_label_is_normalized_for_diagnostic_logs():
    assert _posture_label(None) == "unspecified"
    assert _posture_label("") == "unspecified"
    assert _posture_label(" leaning back / away ") == "leaning-back-away"
