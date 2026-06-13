import pytest

from mg_run_summary import (
    assess_attack_summary,
    assess_repeated_owner_runs,
    assess_summary,
    summarize_file,
    summarize_lines,
)


def test_summary_reports_active_hold_and_low_score_reasons():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:48,759 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE (score=0.834, liveness=0.718)",
        "2026-06-06 11:32:52,722 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.808 liveness=0.729 live=True smooth=0.821 presence=0.808 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou",
        "2026-06-06 11:34:09,201 INFO majestyguard.daemon: Active: low-score face treated as uncertain reason=background_geometry (score=0.479, smooth=0.644, liveness=0.813, quality=0.63, face_h=0.62, center=0.50, raw_faces=1)",
        "2026-06-06 11:34:13,730 INFO majestyguard.daemon: Active frame=225 faces=1 raw_faces=1 owner=True score=0.834 liveness=0.811 live=True smooth=0.827 presence=0.834 quality=0.85 face_h=0.42 center=0.03 select=0.80 reason=sticky_iou",
    ]

    summary = summarize_lines(lines)

    assert summary.entered_active
    assert summary.social_lock_count == 0
    assert summary.locked_count == 0
    assert summary.active_hold_seconds == pytest.approx(84.971, abs=0.001)
    assert summary.state_transitions == {"IDLE->SCANNING": 1, "SCANNING->ACTIVE": 1}
    assert summary.low_score_reasons == {"background_geometry": 1}
    assert summary.active_score.count == 2
    assert summary.active_score.min == pytest.approx(0.808)
    assert summary.active_score.median == pytest.approx(0.821)
    assert summary.active_presence.median == pytest.approx(0.821)
    assert summary.active_liveness.median == pytest.approx(0.770)


def test_summary_counts_social_lock_and_lock_suppression():
    lines = [
        "2026-06-06 10:00:00,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
        "2026-06-06 10:00:10,000 WARNING majestyguard.daemon: STATE: ACTIVE -> SOCIAL_LOCK (stranger while active)",
        "2026-06-06 10:00:10,010 WARNING majestyguard.daemon: LOCK SUPPRESSED (set MG_ENABLE_LOCK=1 to enable real locking)",
        "2026-06-06 10:00:12,000 INFO majestyguard.daemon: Post-lock: returned to IDLE monitoring",
    ]

    summary = summarize_lines(lines)

    assert summary.entered_active
    assert summary.social_lock_count == 1
    assert summary.locked_count == 0
    assert summary.lock_suppressed_count == 1
    assert summary.state_transitions == {"SCANNING->ACTIVE": 1, "ACTIVE->SOCIAL_LOCK": 1}


def test_summary_deduplicates_transition_bursts_from_daemon_logging():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:46,800 INFO majestyguard.daemon: STATE: IDLE -> SCANNING",
        "2026-06-06 11:32:48,759 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE (score=0.834, liveness=0.718)",
        "2026-06-06 11:32:48,759 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
    ]

    summary = summarize_lines(lines)

    assert summary.state_transitions == {"IDLE->SCANNING": 1, "SCANNING->ACTIVE": 1}


def test_summary_reads_powershell_utf16_log_capture(tmp_path):
    log_path = tmp_path / "daemon.out.log"
    log_path.write_text(
        "2026-06-06 12:56:21,823 INFO majestyguard.daemon: STATE: IDLE -> SCANNING\n"
        "2026-06-06 12:56:31,451 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE\n",
        encoding="utf-16",
    )

    summary = summarize_file(log_path)

    assert summary.entered_active
    assert summary.state_transitions == {"IDLE->SCANNING": 1, "SCANNING->ACTIVE": 1}


def test_summary_reports_tracking_smoothing_and_dip_diagnostics():
    lines = [
        "2026-06-06 11:32:48,759 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE (score=0.834, liveness=0.718)",
        "2026-06-06 11:32:52,722 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.808 liveness=0.729 live=True smooth=0.821 presence=0.808 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou candidate=0.808 sticky_iou=0.93 kalman_iou=0.90 template=20 inference=281.3ms",
        "2026-06-06 11:32:57,272 INFO majestyguard.daemon: Active frame=30 faces=1 raw_faces=2 owner=True score=0.533 liveness=0.739 live=True smooth=0.828 presence=0.783 quality=0.86 face_h=0.40 center=0.16 select=0.78 reason=kalman_iou candidate=0.833 sticky_iou=0.95 kalman_iou=0.97 template=20 inference=315.7ms",
        "2026-06-06 11:33:02,272 INFO majestyguard.daemon: Active frame=45 faces=0 raw_faces=0 owner=False score=0.000 liveness=0.000 live=False smooth=0.000 quality=0.00 face_h=0.00 center=0.00 select=0.00 reason=none candidate=0.000 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=107.9ms",
        "2026-06-06 11:34:09,201 INFO majestyguard.daemon: Active: low-score face treated as uncertain reason=background_geometry (score=0.479, smooth=0.644, liveness=0.813, quality=0.63, face_h=0.62, center=0.50, raw_faces=2)",
        "2026-06-06 11:34:09,531 INFO majestyguard.daemon: Active: owner-continuity dip held active reason=recent_owner_smooth (score=0.532, smooth=0.617, liveness=0.813, quality=0.69, face_h=0.50, center=0.21, raw_faces=1)",
        "2026-06-06 11:34:10,201 WARNING majestyguard.daemon: Active: definite stranger (score=0.421, liveness=0.812, smooth=0.410, quality=0.76, face_h=0.45, center=0.11, raw_faces=1, select_reason=identity, sticky_iou=0.02, kalman_iou=0.03, template=7, frames=1/8)",
    ]

    summary = summarize_lines(lines)

    assert summary.active_smooth.count == 2
    assert summary.active_smooth.median == pytest.approx(0.8245)
    assert summary.active_presence.count == 2
    assert summary.active_presence.min == pytest.approx(0.783)
    assert summary.active_no_face_frame_count == 1
    assert summary.active_raw_faces.max == pytest.approx(2.0)
    assert summary.active_sticky_iou.min == pytest.approx(0.93)
    assert summary.active_kalman_iou.max == pytest.approx(0.97)
    assert summary.active_inference_ms.median == pytest.approx(298.5)
    assert summary.selection_reasons == {"sticky_iou": 1, "kalman_iou": 1}
    assert summary.template_hits == {20: 2}
    assert summary.low_score_score.min == pytest.approx(0.479)
    assert summary.low_score_smooth.median == pytest.approx(0.644)
    assert summary.low_score_face_height.max == pytest.approx(0.62)
    assert summary.low_score_center_offset.max == pytest.approx(0.50)
    assert summary.active_continuity_hold_count == 1
    assert summary.active_continuity_reasons == {"recent_owner_smooth": 1}
    assert summary.active_continuity_score.median == pytest.approx(0.532)
    assert summary.active_continuity_smooth.median == pytest.approx(0.617)
    assert summary.definite_stranger_count == 1
    assert summary.definite_stranger_score.median == pytest.approx(0.421)
    assert summary.definite_stranger_reasons == {"active": 1}


def test_summary_counts_scanning_selection_reasons_and_template_hits():
    lines = [
        "2026-06-06 22:08:36,674 INFO majestyguard.daemon: Scanning frame=10 faces=1 raw_faces=1 owner=False score=0.597 liveness=0.763 live=True smooth=0.614 presence=0.650 quality=0.90 face_h=0.67 center=0.25 select=0.85 reason=sticky_iou candidate=0.597 sticky_iou=0.94 kalman_iou=0.94 template=14 inference=388.0ms",
        "2026-06-06 22:08:42,163 INFO majestyguard.daemon: Scanning frame=20 faces=1 raw_faces=1 owner=False score=0.639 liveness=0.765 live=True smooth=0.637 presence=0.650 quality=0.93 face_h=0.67 center=0.18 select=0.89 reason=identity candidate=0.639 sticky_iou=0.84 kalman_iou=0.91 template=30 inference=711.5ms",
        "2026-06-06 22:08:50,010 INFO majestyguard.daemon: Scanning frame=30 faces=1 raw_faces=1 owner=False score=0.637 liveness=0.769 live=True smooth=0.616 presence=0.650 quality=0.93 face_h=0.65 center=0.17 select=0.89 reason=sticky_iou candidate=0.637 sticky_iou=0.96 kalman_iou=0.93 template=30 inference=675.5ms",
    ]

    summary = summarize_lines(lines)

    assert summary.selection_reasons == {"sticky_iou": 2, "identity": 1}
    assert summary.template_hits == {14: 1, 30: 2}


def test_summary_counts_owner_liveness_jitter_continuity_reason():
    lines = [
        "2026-06-06 11:32:48,759 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE (score=0.834, liveness=0.718)",
        "2026-06-06 11:34:09,531 INFO majestyguard.daemon: Active: owner-continuity dip held active reason=owner_liveness_jitter (score=0.720, smooth=0.740, liveness=0.660, quality=0.86, face_h=0.40, center=0.10, raw_faces=1)",
    ]

    summary = summarize_lines(lines)

    assert summary.active_continuity_hold_count == 1
    assert summary.active_continuity_reasons == {"owner_liveness_jitter": 1}
    assert summary.active_continuity_liveness.median == pytest.approx(0.660)


def test_summary_reports_scanning_frame_metrics_for_spoof_evidence():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:47,000 INFO majestyguard.daemon: Scanning frame=15 faces=1 raw_faces=1 owner=False score=0.244 liveness=0.311 live=False smooth=0.244 presence=0.244 quality=0.82 face_h=0.40 center=0.08 select=0.73 reason=identity candidate=0.244 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=188.4ms",
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: Scanning frame=30 faces=1 raw_faces=1 owner=False score=0.277 liveness=0.338 live=False smooth=0.260 presence=0.277 quality=0.83 face_h=0.41 center=0.07 select=0.74 reason=identity candidate=0.277 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=190.4ms",
        "2026-06-06 11:32:49,000 INFO majestyguard.daemon: Scanning frame=45 faces=1 raw_faces=1 owner=False score=0.261 liveness=0.295 live=False smooth=0.261 presence=0.261 quality=0.81 face_h=0.39 center=0.09 select=0.72 reason=identity candidate=0.261 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=186.4ms",
    ]

    summary = summarize_lines(lines)

    assert summary.entered_active is False
    assert summary.scanning_liveness.count == 3
    assert summary.scanning_liveness.median == pytest.approx(0.311)
    assert summary.scanning_score.median == pytest.approx(0.261)
    assert summary.scanning_quality.min == pytest.approx(0.81)


def test_spoof_attack_assessment_passes_when_liveness_stays_low_and_never_enters_active():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:47,000 INFO majestyguard.daemon: Scanning frame=15 faces=1 raw_faces=1 owner=False score=0.244 liveness=0.311 live=False smooth=0.244 presence=0.244 quality=0.82 face_h=0.40 center=0.08 select=0.73 reason=identity candidate=0.244 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=188.4ms",
        "2026-06-06 11:32:48,000 DEBUG majestyguard.daemon: Scanning: uncertain frame reason=low_liveness (score=0.277, smooth=0.260, liveness=0.338, quality=0.83)",
        "2026-06-06 11:32:49,000 INFO majestyguard.daemon: Scanning frame=30 faces=1 raw_faces=1 owner=False score=0.277 liveness=0.338 live=False smooth=0.260 presence=0.277 quality=0.83 face_h=0.41 center=0.07 select=0.74 reason=identity candidate=0.277 sticky_iou=0.00 kalman_iou=0.00 template=-1 inference=190.4ms",
    ]

    assessment = assess_attack_summary(
        summarize_lines(lines),
        scenario="spoof",
        min_attack_evidence_frames=2,
        max_spoof_liveness_median=0.45,
        max_spoof_liveness_max=0.60,
    )

    assert assessment.ok


def test_spoof_attack_assessment_fails_if_spoof_enters_active():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:47,000 INFO majestyguard.daemon: Scanning frame=15 faces=1 raw_faces=1 owner=True score=0.810 liveness=0.791 live=True smooth=0.810 presence=0.810 quality=0.82 face_h=0.40 center=0.08 select=0.73 reason=identity candidate=0.810 sticky_iou=0.00 kalman_iou=0.00 template=2 inference=188.4ms",
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE (score=0.810, liveness=0.791)",
        "2026-06-06 11:32:52,000 INFO majestyguard.daemon: Active frame=30 faces=1 raw_faces=1 owner=True score=0.812 liveness=0.801 live=True smooth=0.811 presence=0.812 quality=0.83 face_h=0.41 center=0.07 select=0.74 reason=identity candidate=0.812 sticky_iou=0.00 kalman_iou=0.00 template=2 inference=190.4ms",
    ]

    assessment = assess_attack_summary(summarize_lines(lines), scenario="spoof")
    failed = {check.name for check in assessment.checks if not check.ok}

    assert not assessment.ok
    assert "entered_active" in failed
    assert "spoof_liveness_median" in failed


def test_stranger_attack_assessment_requires_definite_stranger_and_social_lock_signal():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:47,000 INFO majestyguard.daemon: Scanning: definite stranger (score=0.421, liveness=0.812, smooth=0.410, quality=0.76, face_h=0.45, center=0.11, raw_faces=1, frames=1/3)",
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: Scanning: definite stranger (score=0.418, liveness=0.815, smooth=0.409, quality=0.78, face_h=0.44, center=0.10, raw_faces=1, frames=2/3)",
        "2026-06-06 11:32:49,000 INFO majestyguard.daemon: Scanning: definite stranger (score=0.416, liveness=0.817, smooth=0.407, quality=0.77, face_h=0.44, center=0.09, raw_faces=1, frames=3/3)",
        "2026-06-06 11:32:49,010 WARNING majestyguard.daemon: STATE: SCANNING -> SOCIAL_LOCK (stranger confirmed)",
        "2026-06-06 11:32:49,020 WARNING majestyguard.daemon: LOCK SUPPRESSED (set MG_ENABLE_LOCK=1 to enable real locking)",
    ]

    assessment = assess_attack_summary(
        summarize_lines(lines),
        scenario="stranger",
        min_attack_evidence_frames=3,
        min_social_lock_count=1,
    )

    assert assessment.ok


def test_stranger_attack_assessment_fails_without_definite_stranger_evidence():
    lines = [
        "2026-06-06 11:32:46,798 INFO majestyguard.daemon: STATE: IDLE -> SCANNING (face detected)",
        "2026-06-06 11:32:49,010 WARNING majestyguard.daemon: STATE: SCANNING -> SOCIAL_LOCK (stranger confirmed)",
        "2026-06-06 11:32:49,020 WARNING majestyguard.daemon: LOCK SUPPRESSED (set MG_ENABLE_LOCK=1 to enable real locking)",
    ]

    assessment = assess_attack_summary(
        summarize_lines(lines),
        scenario="stranger",
        min_attack_evidence_frames=3,
        min_social_lock_count=1,
    )
    failed = {check.name for check in assessment.checks if not check.ok}

    assert not assessment.ok
    assert "definite_stranger_count" in failed


def test_summary_assessment_passes_stable_owner_only_run():
    lines = [
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
        "2026-06-06 11:32:52,000 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.808 liveness=0.729 live=True smooth=0.821 presence=0.808 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou",
        "2026-06-06 11:34:00,000 INFO majestyguard.daemon: Active frame=225 faces=1 raw_faces=1 owner=True score=0.834 liveness=0.811 live=True smooth=0.827 presence=0.834 quality=0.85 face_h=0.42 center=0.03 select=0.80 reason=sticky_iou",
    ]

    assessment = assess_summary(summarize_lines(lines), min_active_seconds=60.0)

    assert assessment.ok


def test_summary_assessment_fails_false_social_lock_and_low_median_score():
    lines = [
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
        "2026-06-06 11:32:52,000 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.508 liveness=0.729 live=True smooth=0.521 presence=0.608 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou",
        "2026-06-06 11:33:00,000 WARNING majestyguard.daemon: STATE: ACTIVE -> SOCIAL_LOCK (stranger while active)",
    ]

    assessment = assess_summary(summarize_lines(lines), min_active_seconds=60.0)
    failed = {check.name for check in assessment.checks if not check.ok}

    assert not assessment.ok
    assert "social_lock_count" in failed
    assert "active_score_median" in failed


def _stable_owner_summary(start_minute: int = 32):
    return summarize_lines([
        f"2026-06-06 11:{start_minute:02d}:48,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
        f"2026-06-06 11:{start_minute:02d}:52,000 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.808 liveness=0.729 live=True smooth=0.821 presence=0.808 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou",
        f"2026-06-06 11:{start_minute + 1:02d}:58,000 INFO majestyguard.daemon: Active frame=225 faces=1 raw_faces=1 owner=True score=0.834 liveness=0.811 live=True smooth=0.827 presence=0.834 quality=0.85 face_h=0.42 center=0.03 select=0.80 reason=sticky_iou",
    ])


def test_repeated_owner_run_assessment_requires_multiple_clean_runs():
    assessment = assess_repeated_owner_runs(
        [_stable_owner_summary(32), _stable_owner_summary(35), _stable_owner_summary(38)],
        min_runs=3,
        min_active_seconds=60.0,
    )

    assert assessment.ok
    assert assessment.run_count == 3


def test_repeated_owner_run_assessment_fails_if_any_run_false_social_locks():
    bad = summarize_lines([
        "2026-06-06 11:32:48,000 INFO majestyguard.daemon: STATE: SCANNING -> ACTIVE",
        "2026-06-06 11:32:52,000 INFO majestyguard.daemon: Active frame=15 faces=1 raw_faces=1 owner=True score=0.808 liveness=0.729 live=True smooth=0.821 presence=0.808 quality=0.85 face_h=0.40 center=0.19 select=0.78 reason=sticky_iou",
        "2026-06-06 11:33:58,000 WARNING majestyguard.daemon: STATE: ACTIVE -> SOCIAL_LOCK (stranger while active)",
    ])

    assessment = assess_repeated_owner_runs(
        [_stable_owner_summary(35), bad, _stable_owner_summary(38)],
        min_runs=3,
        min_active_seconds=60.0,
    )
    failed = {check.name for check in assessment.checks if not check.ok}

    assert not assessment.ok
    assert "all_runs_pass_owner_assessment" in failed
    assert "total_social_lock_count" in failed
