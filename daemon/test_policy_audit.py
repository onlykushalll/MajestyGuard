import pytest

from mg_policy_audit import audit_policy


def _failed_names(audit):
    return {check.name for check in audit.checks if not check.ok}


def test_policy_audit_passes_safe_defaults():
    audit = audit_policy({})

    assert audit.ok
    assert audit.lock_enabled is False
    assert audit.whcdf_ipc_enabled is False
    assert audit.service_ipc_enabled is False
    assert audit.adaface_flip_fusion_enabled is True


def test_policy_audit_fails_when_lock_or_whcdf_is_enabled_without_override():
    audit = audit_policy({"MG_ENABLE_LOCK": "1", "MG_ENABLE_WHCDF_IPC": "1"})

    assert not audit.ok
    assert {"lock_disabled", "whcdf_ipc_disabled"} <= _failed_names(audit)


def test_policy_audit_can_explicitly_allow_nondefault_sensitive_flags():
    audit = audit_policy(
        {"MG_ENABLE_LOCK": "1", "MG_ENABLE_WHCDF_IPC": "1"},
        allow_lock_enabled=True,
        allow_whcdf_ipc=True,
    )

    assert audit.ok


def test_policy_audit_flags_disabled_adaface_flip_fusion():
    audit = audit_policy({"MG_ADAFACE_FLIP_FUSION": "0"})

    assert not audit.ok
    assert "adaface_flip_fusion_enabled" in _failed_names(audit)


@pytest.mark.parametrize(
    ("env", "failed"),
    [
        ({"MG_RECOGNITION_THRESHOLD": "0.60"}, "identity_threshold_order"),
        ({"MG_ACTIVE_RECOGNITION_THRESHOLD": "0.50"}, "identity_threshold_order"),
        ({"MG_STRANGER_MAX_SMOOTHED_SCORE": "0.65"}, "continuity_smoothing_buffer"),
        ({"MG_SCANNING_QUICK_OWNER_SCORE": "0.64"}, "quick_owner_score_above_active"),
        ({"MG_SCANNING_QUICK_OWNER_PRESENCE": "0.69"}, "quick_owner_presence_matches_score"),
        ({"MG_SCANNING_QUICK_OWNER_MIN_LIVENESS": "0.70"}, "quick_owner_stronger_liveness"),
        ({"MG_SCANNING_QUICK_OWNER_MIN_QUALITY": "0.78"}, "quick_owner_stronger_quality"),
        ({"MG_SCANNING_QUICK_OWNER_MIN_TRACK_IOU": "0.50"}, "quick_owner_strong_track"),
        ({"MG_SCANNING_QUICK_OWNER_MAX_CENTER_OFFSET": "0.30"}, "quick_owner_foreground_geometry"),
        ({"MG_ACTIVE_CONTINUITY_TRACK_MIN_SCORE": "0.60"}, "track_hold_below_stranger_floor"),
        ({"MG_LIVENESS_THRESHOLD": "0.80"}, "liveness_separate_from_identity"),
        ({"MG_PRESENCE_TRACK_FLOOR": "0.80"}, "presence_confidence_below_unlock"),
        ({"MG_PRESENCE_TRACK_FLOOR": "0.60"}, "presence_confidence_reaches_active"),
        ({"MG_PRESENCE_TRACK_MIN_SCORE": "0.60"}, "presence_track_floor_below_stranger"),
        ({"MG_ACTIVE_LIVENESS_JITTER_FLOOR": "0.72"}, "active_liveness_jitter_below_liveness"),
        ({"MG_ACTIVE_LIVENESS_JITTER_FLOOR": "0.40"}, "active_liveness_jitter_not_too_low"),
        ({"MG_SOFT_LOCK_RELEASE_GRACE_SECONDS": "2.0"}, "soft_lock_release_grace_prevents_bounce"),
        ({"MG_SOFT_LOCK_VERIFY_WINDOW_SECONDS": "30.0"}, "soft_lock_verify_window_bounded"),
        ({"MG_PASSIVE_FPS": "1"}, "passive_camera_near_zero_by_default"),
        ({"MG_BURST_FAST_LIVENESS_THRESHOLD": "0.70"}, "burst_fast_threshold_stricter_than_full"),
        ({"MG_BURST_FAST_CONFIRM_FRAMES": "2"}, "burst_fast_requires_three_frames"),
        ({"MG_BURST_FAST_PATH_SECONDS": "10", "MG_SOFT_LOCK_VERIFY_WINDOW_SECONDS": "8"}, "burst_fast_falls_through_before_window_end"),
        ({"MG_SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES": "2"}, "scanning_ambiguity_grace_covers_confirm"),
        ({"MG_SCANNING_OWNER_AMBIGUITY_MIN_SCORE": "0.60"}, "scanning_ambiguity_score_below_stranger"),
        ({"MG_SCANNING_OWNER_AMBIGUITY_PRESENCE": "0.60"}, "scanning_ambiguity_presence_reaches_active"),
        ({"MG_SCANNING_OWNER_AMBIGUITY_PRESENCE": "0.80"}, "scanning_ambiguity_presence_below_unlock"),
    ],
)
def test_policy_audit_fails_unsafe_threshold_relationships(env, failed):
    audit = audit_policy(env)

    assert failed in _failed_names(audit)


def test_policy_audit_can_require_bounded_unattended_runs():
    unbounded = audit_policy({}, require_bound=True)
    bounded = audit_policy({"MG_MAX_SECONDS": "600"}, require_bound=True)

    assert "bounded_run" in _failed_names(unbounded)
    assert bounded.ok


def test_policy_audit_requires_service_ipc_to_be_explicit_opt_in():
    audit = audit_policy({"MG_ENABLE_SERVICE_IPC": "true"})
    explicit = audit_policy({"MG_ENABLE_SERVICE_IPC": "1"})

    assert "service_ipc_default_off" in _failed_names(audit)
    assert explicit.ok
