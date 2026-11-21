import json

import numpy as np

from camera_health import (
    BACKEND_ANY,
    BACKEND_DSHOW,
    BACKEND_MSMF,
    CameraFault,
    CameraHealth,
    CameraStatus,
    frame_brightness,
    frame_signature,
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _health(**kwargs):
    clock = Clock()
    return CameraHealth(clock=clock, **kwargs), clock


def test_new_health_is_healthy_and_ready():
    health, _ = _health()

    assert health.status is CameraStatus.HEALTHY
    assert health.fault is CameraFault.NONE
    assert health.ready_to_retry() is True
    assert health.diagnosis().headline == "Camera OK"


def test_open_failures_back_off_exponentially_and_cap():
    health, clock = _health(base_delay_s=1.0, max_delay_s=8.0)
    delays = []
    for _ in range(6):
        health.record_open_failure()
        delays.append(round(health.seconds_until_retry(), 3))
        clock.now += 100  # always past the retry time before the next failure

    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_ready_to_retry_waits_for_the_backoff_and_can_be_forced():
    health, clock = _health(base_delay_s=2.0)
    health.record_open_failure()

    assert health.ready_to_retry() is False
    clock.now += 1.9
    assert health.ready_to_retry() is False
    clock.now += 0.2
    assert health.ready_to_retry() is True

    health.record_open_failure()
    assert health.ready_to_retry() is False
    health.request_retry_now()
    assert health.ready_to_retry() is True


def test_success_after_failures_heals_and_counts_a_recovery():
    health, _ = _health()
    health.record_open_failure()
    health.record_open_failure()

    health.record_open_success(BACKEND_MSMF)

    assert health.status is CameraStatus.HEALTHY
    assert health.consecutive_open_failures == 0
    assert health.total_recoveries == 1
    assert health.last_backend == BACKEND_MSMF
    assert health.ready_to_retry() is True


def test_status_escalates_with_repeated_failures():
    health, _ = _health()
    seen = []
    for _ in range(9):
        health.record_open_failure()
        seen.append(health.status)

    assert seen[0] is CameraStatus.DEGRADED
    assert seen[2] is CameraStatus.RECOVERING
    assert seen[-1] is CameraStatus.FAILED


def test_backend_ladder_adds_msmf_then_prefers_the_last_good_backend():
    health, _ = _health()
    health.record_open_success(BACKEND_ANY)
    assert health.backends() == [BACKEND_DSHOW, BACKEND_ANY]

    health.record_open_failure()
    assert health.backends() == [BACKEND_DSHOW, BACKEND_ANY]
    health.record_open_failure()
    assert health.backends() == [BACKEND_DSHOW, BACKEND_ANY, BACKEND_MSMF]
    health.record_open_failure()
    health.record_open_failure()
    assert health.backends()[0] == BACKEND_ANY


def test_privacy_block_is_diagnosed_instead_of_a_generic_open_failure():
    health, _ = _health(privacy_probe=lambda: True)

    fault = health.record_open_failure()

    assert fault is CameraFault.PRIVACY_BLOCKED
    assert "Privacy" in health.diagnosis().hint


def test_a_crashing_privacy_probe_is_ignored():
    def boom():
        raise RuntimeError("registry")

    health, _ = _health(privacy_probe=boom)

    assert health.record_open_failure() is CameraFault.OPEN_FAILED


def test_no_frames_is_a_distinct_fault():
    health, _ = _health()

    assert health.record_read_failure() == 1
    assert health.record_read_failure() == 2
    assert health.record_no_frames() is CameraFault.NO_FRAMES
    assert health.consecutive_read_failures == 0
    assert health.diagnosis().headline == "Camera not responding"


def test_sustained_black_frames_report_a_dark_camera_then_clear():
    health, _ = _health(black_frames_limit=3)

    assert health.observe_frame(0.5, 1) is CameraFault.NONE
    assert health.observe_frame(0.5, 2) is CameraFault.NONE
    assert health.observe_frame(0.5, 3) is CameraFault.BLACK_FRAMES
    assert health.diagnosis().headline == "Camera view is dark"
    assert health.observe_frame(90.0, 4) is CameraFault.NONE
    assert health.fault is CameraFault.NONE


def test_identical_frames_report_a_frozen_driver():
    health, _ = _health(frozen_frames_limit=3)

    results = [health.observe_frame(80.0, 42) for _ in range(5)]

    assert CameraFault.FROZEN_FRAMES in results
    assert health.fault is CameraFault.FROZEN_FRAMES


def test_changing_frames_never_look_frozen():
    health, _ = _health(frozen_frames_limit=3)

    for index in range(50):
        assert health.observe_frame(80.0, index) is CameraFault.NONE


def test_frame_helpers_work_on_real_arrays_and_survive_garbage():
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)

    assert frame_brightness(frame) == 120.0
    assert frame_signature(frame) == frame_signature(frame.copy())
    assert frame_signature(frame) != frame_signature(frame + 1)
    assert frame_brightness(None) is None
    assert frame_signature(None) is None


def test_snapshot_is_json_serialisable_and_written_atomically(tmp_path):
    health, _ = _health()
    health.record_open_failure("boom")
    path = tmp_path / "camera_health.json"

    assert health.write_snapshot(path) is True

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["fault"] == "open_failed"
    assert data["status"] == "degraded"
    assert data["backends"] == ["dshow", "any"]
    assert not (tmp_path / "camera_health.json.tmp").exists()


def test_write_snapshot_never_raises_on_bad_path(tmp_path):
    health, _ = _health()

    assert health.write_snapshot(tmp_path / "missing_dir" / "x.json") is False
