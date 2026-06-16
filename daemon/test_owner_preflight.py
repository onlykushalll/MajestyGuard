from mg_owner_preflight import _classify_preflight


def test_preflight_warns_but_allows_dev_validation_above_daemon_floor():
    decision = _classify_preflight(
        max_score=0.793,
        median_height=0.46,
        median_offset=0.14,
        median_quality=0.80,
        dev_score=0.78,
        ready_score=0.82,
        min_face_height=0.24,
        max_center_offset=0.45,
        min_quality=0.45,
    )

    assert decision.status == "CAUTION"
    assert decision.exit_code == 0
    assert "production margin" in decision.message


def test_preflight_waits_below_recognition_floor_even_if_liveness_threshold_is_lower():
    decision = _classify_preflight(
        max_score=0.72,
        median_height=0.46,
        median_offset=0.14,
        median_quality=0.80,
        dev_score=0.78,
        ready_score=0.82,
        min_face_height=0.24,
        max_center_offset=0.45,
        min_quality=0.45,
    )

    assert decision.status == "WAIT"
    assert decision.exit_code == 1
    assert "recognition" in decision.message


def test_preflight_ready_when_identity_margin_is_strong():
    decision = _classify_preflight(
        max_score=0.86,
        median_height=0.46,
        median_offset=0.14,
        median_quality=0.80,
        dev_score=0.78,
        ready_score=0.82,
        min_face_height=0.24,
        max_center_offset=0.45,
        min_quality=0.45,
    )

    assert decision.status == "READY"
    assert decision.exit_code == 0


def test_preflight_geometry_and_quality_still_block_before_score_margin():
    too_small = _classify_preflight(
        max_score=0.90,
        median_height=0.12,
        median_offset=0.14,
        median_quality=0.80,
        dev_score=0.78,
        ready_score=0.82,
        min_face_height=0.24,
        max_center_offset=0.45,
        min_quality=0.45,
    )
    low_quality = _classify_preflight(
        max_score=0.90,
        median_height=0.46,
        median_offset=0.14,
        median_quality=0.30,
        dev_score=0.78,
        ready_score=0.82,
        min_face_height=0.24,
        max_center_offset=0.45,
        min_quality=0.45,
    )

    assert too_small.status == "WAIT"
    assert low_quality.status == "WAIT"
