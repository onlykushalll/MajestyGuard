import threading
from types import SimpleNamespace
import pytest

import main as daemon_main
from companion_ipc import FaceState
from main import MajestyGuardDaemon, State


def test_cold_start_camera_time_excludes_idle_wait(monkeypatch):
    from unittest.mock import Mock

    daemon = Mock()
    daemon._is_tearing_down = False
    daemon._start_and_run.side_effect = lambda: MajestyGuardDaemon._start_and_run(daemon)
    daemon._idle_check_mode = True
    daemon._persistent_idle_loop = False
    daemon.state = State.IDLE
    daemon._stop.is_set.return_value = False
    daemon._current_idle_timeout.return_value = 60
    daemon._start_idle_probe.side_effect = lambda **kw: MajestyGuardDaemon._start_idle_probe(daemon, **kw)
    monkeypatch.setattr(daemon_main, "WHCDF_IPC_ENABLED", False)
    monkeypatch.setattr(daemon_main, "SERVICE_IPC_ENABLED", False)
    monkeypatch.setattr(daemon_main, "CMDServer", Mock())
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 65)
    monkeypatch.setattr(daemon_main.time, "sleep", lambda _: None)
    ticks = iter([0.0, .4, 65.0, 65.3])
    monkeypatch.setattr(daemon_main.time, "perf_counter",
                        lambda: .6 if threading.current_thread().name.startswith("mg-warmup") else next(ticks))

    MajestyGuardDaemon.start(daemon)

    assert daemon._cold_start_ms == {
        "model_load": 400, "warmup": 200, "camera_open": 300, "total": 900,
    }
    daemon._open_camera.assert_called_once_with()


def test_late_idle_opens_camera_during_warmup_but_never_scans_early(monkeypatch):
    from unittest.mock import Mock
    daemon = Mock()
    daemon._is_tearing_down = False
    daemon._start_and_run.side_effect = lambda: MajestyGuardDaemon._start_and_run(daemon)
    daemon._idle_check_mode = True
    daemon._persistent_idle_loop = False
    daemon.state = State.IDLE
    daemon._stop = threading.Event()
    daemon._current_idle_timeout.return_value = 60
    daemon._start_idle_probe.side_effect = lambda **kw: MajestyGuardDaemon._start_idle_probe(daemon, **kw)
    monkeypatch.setattr(daemon_main, "CMDServer", Mock())
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 65)
    monkeypatch.setattr(daemon_main, "SERVICE_IPC_ENABLED", False)
    monkeypatch.setattr(daemon_main, "WHCDF_IPC_ENABLED", False)
    camera_open, warm_done = threading.Event(), threading.Event()
    def warm():
        assert camera_open.wait(2)
        warm_done.set()
    def opening():
        camera_open.set()
        return True
    def broadcast(name, **kw):
        if name == "scanning":
            assert warm_done.is_set()
    daemon._warmup_inference.side_effect = warm
    daemon._open_camera.side_effect = opening
    daemon.ipc.broadcast_state.side_effect = broadcast
    MajestyGuardDaemon.start(daemon)
    daemon._run_loop.assert_called_once()

@pytest.fixture(autouse=True)
def mock_timer(monkeypatch):
    class FakeTimer:
        def __init__(self, interval, function, args=None, kwargs=None):
            self.function = function
            self.args = args or []
            self.kwargs = kwargs or {}
        def start(self):
            # No-op: do not spawn background threads
            pass
        def cancel(self):
            pass
    monkeypatch.setattr(threading, "Timer", FakeTimer)


class FakeIpc:
    def __init__(self):
        self.states = []

    def broadcast_state(self, state, **kwargs):
        self.states.append((state, kwargs))


class FakeMotion:
    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1


class FakeFaceEngine:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.reset_liveness_calls = 0
        self.process_frame_calls = 0
        self.liveness_modes = []

    def reset_liveness(self):
        self.reset_liveness_calls += 1

    def process_frame(self, _frame, *, liveness_mode="full"):
        self.process_frame_calls += 1
        self.liveness_modes.append(liveness_mode)
        if self._results:
            return self._results.pop(0)
        return SimpleNamespace(face_count=0)


class FakeCap:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


def _bare_daemon(state=State.ACTIVE, face_engine=None):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = state
    daemon.ipc = FakeIpc()
    daemon.motion = FakeMotion()
    daemon._stop = threading.Event()
    daemon._absent_frames = 0
    daemon._stranger_frames = 0
    daemon._active_reacquire_grace_frames = 0
    daemon._owner_continuity_grace_frames = 0
    daemon._scanning_owner_ambiguity_grace_frames = 0
    daemon._cap = None
    daemon._camera_read_failures = 0
    daemon._input_idle_soft_lock_armed = True
    daemon._soft_lock_release_grace_until = 0.0
    daemon._soft_lock_verify_until = 0.0
    daemon._soft_lock_verification_started_at = 0.0
    daemon._soft_lock_owner_candidate_frames = 0
    daemon._soft_lock_fast_pass_frames = 0
    daemon.face_eng = face_engine or FakeFaceEngine()
    daemon.service_ipc = None
    daemon.command_ipc = None
    daemon._overlay_proc = None
    daemon._overlay_watchdog_thread = None
    daemon._whcdf_stop = threading.Event()
    daemon._is_tearing_down = False
    return daemon


def _result(
    *,
    score=0.0,
    smooth=None,
    liveness=0.0,
    quality=0.0,
    height=0.0,
    center=0.0,
    raw_faces=1,
    face_count=1,
    presence=None,
    selection_reason="identity",
    sticky_iou=0.0,
    predicted_iou=0.0,
):
    if smooth is None:
        smooth = score
    if presence is None:
        presence = score
    return SimpleNamespace(
        face_count=face_count,
        raw_face_count=raw_faces,
        recognition_score=score,
        smoothed_recognition_score=smooth,
        presence_confidence=presence,
        liveness_score=liveness,
        liveness_passed=liveness >= daemon_main.LIVENESS_THRESHOLD,
        frame_quality=quality,
        face_height_frac=height,
        face_center_offset=center,
        selected_face_score=0.90,
        selection_reason=selection_reason,
        candidate_owner_score=score,
        sticky_iou=sticky_iou,
        predicted_iou=predicted_iou,
        best_template_index=1,
        inference_ms=1.0,
        primary_user_present=False,
        virtual_camera_detected=False,
        camera_obstructed=False,
    )


def test_idle_timeout_enters_soft_lock_without_calling_windows_lock(monkeypatch):
    called = []
    monkeypatch.setattr(daemon_main, "lock_workstation", lambda: called.append("lock"))
    daemon = _bare_daemon(State.ACTIVE)

    daemon._enter_soft_lock("idle_timeout")

    assert daemon.state == State.SOFT_LOCK
    assert daemon.ipc.states[-1][0] == "locked_passive"
    assert daemon.ipc.states[-1][1]["detail"] == "idle_timeout"
    assert called == []


def test_owner_verification_clears_soft_lock_and_restores_active():
    daemon = _bare_daemon(State.SOFT_LOCK)
    FaceState.clear()

    daemon._clear_soft_lock(confidence=0.91, liveness=0.83)

    assert daemon.state == State.ACTIVE
    assert daemon.ipc.states[-1][0] == "active"
    assert daemon.ipc.states[-1][1]["confidence"] == 0.91
    assert FaceState.is_authorized()[0] is True


def test_passive_soft_lock_startup_does_not_open_camera_until_verify_requested(monkeypatch):
    daemon = _bare_daemon(State.SOFT_LOCK)
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)

    assert daemon._should_open_camera_on_start() is False
    daemon._soft_lock_verify_until = 115.0
    assert daemon._should_open_camera_on_start() is True


def test_stranger_while_soft_locked_keeps_shield_and_enters_social_lock():
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._soft_lock_stranger()

    assert daemon.state == State.SOCIAL_LOCK
    assert daemon.ipc.states[-1][0] == "social_lock"


def test_inactivity_lock_does_not_restrict_background_processes():
    daemon = _bare_daemon(State.ACTIVE)

    daemon._enter_soft_lock("idle_timeout")

    assert not getattr(daemon, "_background_processes_restricted", False)


def test_active_input_idle_threshold_enters_soft_lock(monkeypatch):
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 121.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_IDLE_SECONDS", 90.0)
    daemon = _bare_daemon(State.ACTIVE)

    locked = daemon._maybe_enter_soft_lock_for_input_idle()

    assert locked is True
    assert daemon.state == State.SOFT_LOCK
    assert daemon.ipc.states[-1][0] == "locked_passive"
    assert daemon.ipc.states[-1][1]["detail"] == "input_idle_121s"


def test_face_unlock_does_not_immediately_relock_on_stale_windows_idle(monkeypatch):
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 121.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_IDLE_SECONDS", 90.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_RELEASE_GRACE_SECONDS", 15.0)
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._clear_soft_lock(confidence=0.91, liveness=0.83)
    locked = daemon._maybe_enter_soft_lock_for_input_idle()

    assert locked is False
    assert daemon.state == State.ACTIVE
    assert daemon.ipc.states[-1][0] == "active"


def test_face_unlock_idle_rearms_after_real_user_input(monkeypatch):
    idle_values = iter([121.0, 0.2, 121.0])
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: next(idle_values))
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_IDLE_SECONDS", 90.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_IDLE_REARM_SECONDS", 1.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_RELEASE_GRACE_SECONDS", 15.0)
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._clear_soft_lock(confidence=0.91, liveness=0.83)
    assert daemon._maybe_enter_soft_lock_for_input_idle() is False
    assert daemon._maybe_enter_soft_lock_for_input_idle() is False
    assert daemon._maybe_enter_soft_lock_for_input_idle() is True

    assert daemon.state == State.SOFT_LOCK
    assert daemon.ipc.states[-1][0] == "locked_passive"


def test_face_unlock_idle_relocks_after_grace_expires(monkeypatch):
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 121.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_IDLE_SECONDS", 90.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_RELEASE_GRACE_SECONDS", 15.0)
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._clear_soft_lock(confidence=0.91, liveness=0.83)
    now["value"] = 116.0

    assert daemon._maybe_enter_soft_lock_for_input_idle() is True
    assert daemon.state == State.SOFT_LOCK


def test_soft_lock_is_passive_until_user_requests_verification(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._tick_soft_lock(frame=object(), frame_no=1)

    assert daemon.face_eng.process_frame_calls == 0
    assert daemon.ipc.states[-1] == ("locked_passive", {"detail": "Press Space to verify"})


def test_soft_lock_verification_command_opens_short_verify_window(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(daemon_main, "SOFT_LOCK_VERIFY_WINDOW_SECONDS", 12.0)
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._handle_ui_command("verify_requested", "space")

    assert daemon._soft_lock_verify_until == 112.0
    assert daemon._soft_lock_verification_started_at == 100.0
    assert daemon.face_eng.reset_liveness_calls == 1
    assert daemon.ipc.states[-1] == ("verifying_lock", {"detail": "Face verification"})


def test_soft_lock_passive_branch_releases_camera(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)
    cap = FakeCap()
    daemon._cap = cap

    assert daemon._is_soft_lock_passive() is True
    daemon._release_camera()

    assert cap.released is True
    assert daemon._cap is None


def test_soft_lock_never_opens_camera_without_explicit_verify_request(monkeypatch):
    """The passive overlay stays camera-cold even if a legacy FPS setting exists."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    opens = []
    daemon._loop_wake = threading.Event()
    monkeypatch.setattr(daemon_main, "PASSIVE_LOOP_SLEEP_S", 0)
    monkeypatch.setattr(daemon_main, "PASSIVE_FPS", 1.0)
    monkeypatch.setattr(daemon_main.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(daemon, "_open_camera", lambda **_kwargs: opens.append("open") or True)

    daemon._handle_passive_soft_lock()

    assert opens == []


def test_soft_lock_verification_timeout_returns_to_passive_state(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._soft_lock_verify_until = 105.0

    assert daemon._is_soft_lock_passive() is False
    now["value"] = 106.0

    assert daemon._is_soft_lock_passive() is True
    assert daemon.ipc.states[-1] == ("verify_failed", {})


def test_space_after_deadline_cannot_restart_verification_before_cooldown(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._start_soft_lock_verification("space")
    now["value"] = daemon._soft_lock_verify_until + 0.01

    daemon._handle_ui_command("verify_requested", "space")

    assert daemon._soft_lock_verify_until == 0.0
    assert daemon._verify_fail_streak == 1
    assert daemon._verify_cooldown_until > now["value"]
    assert daemon.face_eng.reset_liveness_calls == 1
    assert daemon.ipc.states[-1][0] == "verify_failed"


def test_space_crossing_deadline_during_dispatch_cannot_replace_pending_attempt(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._start_soft_lock_verification("space")
    deadline = daemon._soft_lock_verify_until
    times = iter((deadline, deadline + 0.01))
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: next(times, deadline + 0.01))

    daemon._handle_ui_command("verify_requested", "space")
    daemon._expire_soft_lock_verification_if_needed()

    assert daemon._soft_lock_verify_until == 0.0
    assert daemon._verify_fail_streak == 1
    assert daemon.face_eng.reset_liveness_calls == 1


def test_owner_result_after_deadline_cannot_unlock(monkeypatch, tmp_path):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._start_soft_lock_verification("space")
    deadline = daemon._soft_lock_verify_until
    now["value"] = deadline - 0.1

    def slow_owner_frame(_frame, *, liveness_mode):
        assert liveness_mode == "full"
        now["value"] = deadline + 0.01
        return _result(score=0.99, liveness=0.99, quality=0.99, height=0.50)

    monkeypatch.setattr(daemon.face_eng, "process_frame", slow_owner_frame)

    daemon._tick_soft_lock(frame=object(), frame_no=1)

    assert daemon.state == State.SOFT_LOCK
    assert daemon._soft_lock_verify_until == 0.0
    assert daemon._verify_cooldown_until > now["value"]
    assert [state for state, _ in daemon.ipc.states] == ["verifying_lock", "verify_failed"]


def test_verification_timing_reports_first_stage_times_once(monkeypatch, tmp_path, caplog):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon_main, "BURST_FAST_PATH_SECONDS", 0.5)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._start_soft_lock_verification("space")
    now["value"] = 100.25
    daemon._mark_verification_phase("camera_ready")
    now["value"] = 100.50
    daemon._mark_verification_phase("camera_ready")
    daemon._mark_verification_phase("first_frame")
    now["value"] = 101.0

    def owner_frame(_frame, *, liveness_mode):
        assert liveness_mode == "full"
        now["value"] = 101.25
        return _result(score=0.99, liveness=0.99, quality=0.99, height=0.50)

    monkeypatch.setattr(daemon.face_eng, "process_frame", owner_frame)
    with caplog.at_level("INFO", logger=daemon_main.log.name):
        daemon._tick_soft_lock(frame=object(), frame_no=1)

    attempts = [line for line in caplog.messages if line.startswith("[VerifyTiming]")]
    assert attempts == [
        "[VerifyTiming] outcome=owner_verified camera_ready_ms=250 first_frame_ms=500 "
        "full_started_ms=1000 first_result_ms=1250 decision_ms=1250"
    ]
    assert daemon._verification_phase_ms is None


def test_soft_lock_burst_uses_fast_liveness_before_full_path(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "BURST_FAST_PATH_SECONDS", 5.0)
    face_engine = FakeFaceEngine([
        _result(score=0.79, liveness=0.84, quality=0.90, height=0.50, center=0.10, presence=0.80),
    ])
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._start_soft_lock_verification("space")

    daemon._tick_soft_lock(frame=object(), frame_no=1)

    assert face_engine.liveness_modes == ["fast"]


def test_soft_lock_burst_falls_through_to_full_liveness_after_fast_window(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "BURST_FAST_PATH_SECONDS", 5.0)
    face_engine = FakeFaceEngine([
        _result(score=0.79, liveness=0.84, quality=0.90, height=0.50, center=0.10, presence=0.80),
    ])
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._start_soft_lock_verification("space")
    now["value"] = 106.0

    daemon._tick_soft_lock(frame=object(), frame_no=6)

    assert face_engine.liveness_modes == ["full"]


def test_soft_lock_strict_fast_path_requires_three_passes_before_active(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "BURST_FAST_LIVENESS_THRESHOLD", 0.82)
    monkeypatch.setattr(daemon_main, "BURST_FAST_CONFIRM_FRAMES", 3)
    face_engine = FakeFaceEngine([
        _result(score=0.79, liveness=0.84, quality=0.90, height=0.50, center=0.10, presence=0.80),
        _result(score=0.80, liveness=0.85, quality=0.91, height=0.50, center=0.10, presence=0.81),
        _result(score=0.81, liveness=0.86, quality=0.92, height=0.50, center=0.10, presence=0.82),
    ])
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._start_soft_lock_verification("space")

    daemon._tick_soft_lock(frame=object(), frame_no=1)
    assert daemon.state == State.SOFT_LOCK
    daemon._tick_soft_lock(frame=object(), frame_no=2)
    assert daemon.state == State.SOFT_LOCK
    daemon._tick_soft_lock(frame=object(), frame_no=3)

    assert daemon.state == State.ACTIVE
    assert face_engine.liveness_modes == ["fast", "fast", "fast"]


def test_soft_lock_fast_consensus_restarts_after_a_rejected_sample(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(daemon_main, "BURST_FAST_CONFIRM_FRAMES", 3)
    passed = _result(score=0.85, liveness=0.88, quality=0.92)
    rejected = _result(score=0.84, liveness=0.75, quality=0.92)
    face_engine = FakeFaceEngine([passed, passed, rejected, passed, passed, passed])
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._start_soft_lock_verification("space")

    for frame_no in range(1, 6):
        daemon._tick_soft_lock(frame=object(), frame_no=frame_no)
        assert daemon.state == State.SOFT_LOCK
    daemon._tick_soft_lock(frame=object(), frame_no=6)
    assert daemon.state == State.ACTIVE


def test_hostile_lock_is_the_only_transition_that_calls_windows_lock(monkeypatch):
    called = []
    monkeypatch.setattr(daemon_main, "lock_workstation", lambda: called.append("lock"))
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._transition(State.SOCIAL_LOCK)
    assert called == []

    daemon._transition(State.HOSTILE_LOCK)
    assert called == ["lock"]


def test_overlay_watchdog_restarts_overlay_only_while_locked(monkeypatch):
    launches = []
    daemon = _bare_daemon(State.IDLE)
    daemon._overlay_proc = SimpleNamespace(poll=lambda: 1)
    monkeypatch.setattr(daemon, "_launch_overlay", lambda: launches.append("launch"))

    daemon._ensure_overlay_alive_if_needed()
    assert launches == []

    daemon.state = State.SOFT_LOCK
    daemon._ensure_overlay_alive_if_needed()
    assert launches == ["launch"]


def test_soft_lock_fast_owner_consensus_clears_overlay_below_strict_peak(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    FaceState.clear()
    face_engine = FakeFaceEngine(
        [
            _result(score=0.73, smooth=0.72, liveness=0.86, quality=0.86, height=0.48, center=0.12, presence=0.73),
            _result(score=0.74, smooth=0.73, liveness=0.87, quality=0.87, height=0.50, center=0.13, presence=0.74),
        ]
    )
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._soft_lock_verify_until = 112.0

    daemon._tick_soft_lock(frame=object(), frame_no=1)
    daemon._tick_soft_lock(frame=object(), frame_no=2)

    assert daemon.state == State.ACTIVE
    assert daemon._soft_lock_owner_candidate_frames == 0
    authorized, reason = FaceState.is_authorized()
    assert authorized
    assert reason == "ok"
    FaceState.clear()


def test_soft_lock_quick_owner_consensus_clears_overlay_with_strong_track(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    FaceState.clear()
    face_engine = FakeFaceEngine(
        [
            _result(
                score=0.707,
                smooth=0.716,
                liveness=0.83,
                quality=0.80,
                height=0.37,
                center=0.24,
                presence=0.716,
                selection_reason="sticky_iou",
                sticky_iou=0.97,
            ),
            _result(
                score=0.700,
                smooth=0.706,
                liveness=0.85,
                quality=0.80,
                height=0.37,
                center=0.24,
                presence=0.706,
                selection_reason="kalman_iou",
                sticky_iou=0.92,
                predicted_iou=0.93,
            ),
        ]
    )
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._soft_lock_verify_until = 112.0

    daemon._tick_soft_lock(frame=object(), frame_no=1)
    daemon._tick_soft_lock(frame=object(), frame_no=2)

    assert daemon.state == State.ACTIVE
    assert daemon.ipc.states[-1][0] == "active"
    FaceState.clear()


def test_soft_lock_fast_owner_consensus_keeps_security_gates(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    FaceState.clear()
    face_engine = FakeFaceEngine(
        [
            _result(score=0.74, smooth=0.73, liveness=0.69, quality=0.88, height=0.50, center=0.12, presence=0.74),
            _result(score=0.75, smooth=0.74, liveness=0.69, quality=0.89, height=0.50, center=0.12, presence=0.75),
            _result(score=0.74, smooth=0.73, liveness=0.78, quality=0.70, height=0.50, center=0.12, presence=0.74),
            _result(score=0.75, smooth=0.74, liveness=0.78, quality=0.70, height=0.50, center=0.12, presence=0.75),
            _result(score=0.74, smooth=0.73, liveness=0.78, quality=0.88, height=0.50, center=0.36, presence=0.74),
            _result(score=0.75, smooth=0.74, liveness=0.78, quality=0.89, height=0.50, center=0.36, presence=0.75),
            _result(score=0.74, smooth=0.73, liveness=0.78, quality=0.88, height=0.50, center=0.12, presence=0.74, raw_faces=2),
            _result(score=0.75, smooth=0.74, liveness=0.78, quality=0.89, height=0.50, center=0.12, presence=0.75, raw_faces=2),
        ]
    )
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._soft_lock_verify_until = 112.0

    for frame_no in range(1, 9):
        daemon._tick_soft_lock(frame=object(), frame_no=frame_no)

    assert daemon.state == State.SOFT_LOCK
    assert daemon._soft_lock_owner_candidate_frames == 0
    assert FaceState.is_authorized()[0] is False
    FaceState.clear()


def test_windows_lock_request_cannot_release_lock_without_session_event(tmp_path, monkeypatch):
    from session_monitor import SessionEvent
    # Mock _MG_STATE_DIR in daemon_main
    mock_state_dir = tmp_path / "state"
    mock_state_dir.mkdir()
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", mock_state_dir)
    
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._exit_at = None
    
    lock_state_file = mock_state_dir / "lock_state.txt"
    lock_state_file.write_text("LOCKED\n", encoding="utf-8")
    daemon._handle_ui_command("windows_lock_used", "ui")
    assert daemon._exit_at is None and daemon.state is State.SOFT_LOCK
    assert lock_state_file.read_text(encoding="utf-8").strip() == "LOCKED"
    daemon._handle_session_event(SessionEvent.SESSION_LOCK)
    assert daemon.state is State.SYSTEM_LOCKED
    assert daemon.ipc.states[-1][0] == "locked"

def test_repeated_stranger_strikes_while_locked_escalate_to_hostile_lock(monkeypatch):
    called = []
    monkeypatch.setattr(daemon_main, "lock_workstation", lambda: called.append("lock"))
    monkeypatch.setattr(daemon_main, "MAX_LOCK_STRANGER_STRIKES", 3)
    daemon = _bare_daemon(State.SOFT_LOCK)

    daemon._soft_lock_stranger()
    assert daemon.state == State.SOCIAL_LOCK
    assert called == []

    daemon._soft_lock_stranger()
    assert daemon.state == State.SOCIAL_LOCK
    assert called == []

    daemon._soft_lock_stranger()
    assert daemon.state == State.HOSTILE_LOCK
    assert called == ["lock"]


def test_verify_requested_during_cooldown_broadcasts_verify_failed_detail(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._verify_cooldown_until = 104.0

    daemon._handle_ui_command("verify_requested", "space")

    assert daemon.ipc.states[-1][0] == "verify_failed"
    assert "cooldown" in daemon.ipc.states[-1][1].get("detail", "").lower()
