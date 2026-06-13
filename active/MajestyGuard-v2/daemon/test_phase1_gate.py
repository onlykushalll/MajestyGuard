from pathlib import Path

from phase1_gate import (
    PhaseGateInputs,
    _read_log_text,
    evaluate_phase1,
    parse_liveness_text,
    parse_recognition_text,
)


def test_parse_recognition_text_extracts_required_metrics():
    metrics = parse_recognition_text(
        """
        Recognition diagnostic summary
          posture: upright
          samples: 88
          no_embedding_frames: 0
          max: 0.872
          median: 0.813
        """
    )

    assert metrics.posture == "upright"
    assert metrics.max_score == 0.872
    assert metrics.median_score == 0.813
    assert metrics.no_embedding_frames == 0


def test_parse_liveness_text_extracts_required_metrics():
    metrics = parse_liveness_text(
        """
        Layer diagnostic summary
          skipped_quality_frames: 4
          total_frames: 100
          onnx       median=0.960 p10=0.930 p90=0.990 mean=0.955
          smoothed   median=0.801 p10=0.721 p90=0.840
        """
    )

    assert metrics.p10 == 0.721
    assert metrics.p50 == 0.801
    assert metrics.onnx_mean == 0.955
    assert metrics.skipped_quality_percent == 4.0


def test_read_log_text_accepts_powershell_tee_utf16(tmp_path: Path):
    path = tmp_path / "liveness.txt"
    path.write_text("onnx median=0.950 p10=0.920 p90=0.980 mean=0.951\n", encoding="utf-16")

    assert _read_log_text(path).startswith("onnx median=0.950")


def test_phase1_gate_passes_when_all_offline_metrics_meet_contract():
    result = evaluate_phase1(
        PhaseGateInputs(
            selected_templates=56,
            mean_pairwise=0.74,
            min_pairwise=0.52,
            outlier_count=0,
            recognition_runs=[
                parse_recognition_text("posture: upright\nmax: 0.872\nmedian: 0.813\nno_embedding_frames: 0"),
                parse_recognition_text("posture: leaning\nmax: 0.861\nmedian: 0.806\nno_embedding_frames: 0"),
                parse_recognition_text("posture: tilted\nmax: 0.855\nmedian: 0.802\nno_embedding_frames: 0"),
            ],
            liveness=parse_liveness_text(
                "total_frames: 100\nskipped_quality_frames: 3\n"
                "onnx median=0.950 p10=0.920 p90=0.980 mean=0.951\n"
                "smoothed median=0.790 p10=0.710 p90=0.830"
            ),
            daemon_entered_active=True,
            daemon_active_hold_seconds=65.0,
            daemon_social_lock_count=0,
        )
    )

    assert result.status == "PASS"
    assert not result.failed_checks


def test_phase1_gate_fails_when_recognition_or_liveness_is_weak():
    result = evaluate_phase1(
        PhaseGateInputs(
            selected_templates=40,
            mean_pairwise=0.74,
            min_pairwise=0.52,
            outlier_count=0,
            recognition_runs=[
                parse_recognition_text("posture: tilted\nmax: 0.801\nmedian: 0.760\nno_embedding_frames: 0"),
            ],
            liveness=parse_liveness_text(
                "total_frames: 100\nskipped_quality_frames: 30\n"
                "onnx median=0.850 p10=0.800 p90=0.900 mean=0.850\n"
                "smoothed median=0.760 p10=0.650 p90=0.810"
            ),
            daemon_entered_active=False,
            daemon_active_hold_seconds=0.0,
            daemon_social_lock_count=1,
        )
    )

    assert result.status == "FAIL"
    assert "recognition_tilted_median" in result.failed_checks
    assert "liveness_p10" in result.failed_checks
    assert "daemon_entered_active" in result.failed_checks


def test_phase1_gate_cautions_when_live_evidence_is_postponed_but_artifacts_pass():
    result = evaluate_phase1(
        PhaseGateInputs(
            selected_templates=56,
            mean_pairwise=0.74,
            min_pairwise=0.52,
            outlier_count=0,
        )
    )

    assert result.status == "CAUTION"
    assert not result.failed_checks
    assert "recognition_run_count" in result.caution_checks
    assert "liveness_present" in result.caution_checks
    assert "daemon_run_present" in result.caution_checks
