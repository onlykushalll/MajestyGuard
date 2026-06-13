import json

import pytest

from attack_tester import (
    ATTACK_SCENARIOS,
    AttackResult,
    classify_attack_result,
    parse_frame_metrics,
    validate_attack_result_schema,
)


def test_attack_result_schema_validation_accepts_expected_fields():
    result = AttackResult(
        attack="printed_photo",
        description="A4 printed photo of owner held in front of camera",
        daemon_response="SCANNING",
        seconds_to_response=4.2,
        recognition_score_seen=0.712,
        liveness_score_seen=0.31,
        liveness_passed=False,
        result="BLOCKED",
        notes="Liveness stayed below threshold.",
    )

    data = result.to_dict()

    validate_attack_result_schema(data)
    json.dumps(data)


def test_attack_result_schema_rejects_fabricated_result_status():
    data = AttackResult(
        attack="printed_photo",
        description="A4 printed photo of owner held in front of camera",
        daemon_response="ACTIVE",
        seconds_to_response=2.0,
        recognition_score_seen=0.91,
        liveness_score_seen=0.82,
        liveness_passed=True,
        result="MAGIC",
        notes="bad",
    ).to_dict()

    with pytest.raises(ValueError):
        validate_attack_result_schema(data)


def test_parse_frame_metrics_extracts_scores_and_liveness_flag():
    line = (
        "2026-06-08 15:00:00 INFO majestyguard.daemon: "
        "Scanning frame=30 faces=1 raw_faces=1 owner=False score=0.597 "
        "liveness=0.763 live=True smooth=0.614 presence=0.650 "
        "quality=0.90 face_h=0.67 center=0.25 select=0.85 "
        "reason=identity candidate=0.597 sticky_iou=0.94 "
        "kalman_iou=0.94 template=14 inference=388.0ms"
    )

    metrics = parse_frame_metrics(line)

    assert metrics["recognition_score"] == pytest.approx(0.597)
    assert metrics["liveness_score"] == pytest.approx(0.763)
    assert metrics["liveness_passed"] is True


def test_classify_marks_active_attack_as_bypassed():
    assert classify_attack_result("printed_photo", ["SCANNING", "ACTIVE"], True) == "BYPASSED"


def test_all_required_attack_scenarios_are_declared():
    names = {scenario.attack for scenario in ATTACK_SCENARIOS}

    assert {
        "printed_photo",
        "phone_screen_replay",
        "camera_obstruction",
        "camera_unplug",
        "virtual_camera_injection",
        "second_person_at_laptop",
        "rapid_face_swap",
        "low_light_bypass",
    } <= names
