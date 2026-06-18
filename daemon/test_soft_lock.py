import threading
from types import SimpleNamespace
import pytest

import main as daemon_main
from companion_ipc import FaceState
from main import MajestyGuardDaemon, State

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


def test_soft_lock_verification_timeout_returns_to_passive_state(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._soft_lock_verify_until = 105.0

    assert daemon._is_soft_lock_passive() is False
    now["value"] = 106.0

    assert daemon._is_soft_lock_passive() is True
    assert daemon.ipc.states[-1] == ("verify_failed", {})


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
            _result(score=0.73, smooth=0.72, liveness=0.76, quality=0.86, height=0.48, center=0.12, presence=0.73),
            _result(score=0.74, smooth=0.73, liveness=0.77, quality=0.87, height=0.50, center=0.13, presence=0.74),
        ]
    )
    daemon = _bare_daemon(State.SOFT_LOCK, face_engine)
    daemon._soft_lock_verify_until = 112.0

    daemon._tick_soft_lock(frame=object(), frame_no=1)
    daemon._tick_soft_lock(frame=object(), frame_no=2)

    assert daemon.state == State.ACTIVE
    assert daemon._soft_lock_owner_candidate_frames == 0
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason.startswith("liveness-below-threshold")
    FaceState.clear()


def test_soft_lock_quick_owner_consensus_clears_overlay_with_strong_track(monkeypatch):
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 100.0)
    FaceState.clear()
    face_engine = FakeFaceEngine(
        [
            _result(
                score=0.707,
                smooth=0.716,
                liveness=0.750,
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
                liveness=0.774,
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


def test_windows_lock_used_command_schedules_exit(tmp_path, monkeypatch):
    import time
    # Mock _MG_STATE_DIR in daemon_main
    mock_state_dir = tmp_path / "state"
    mock_state_dir.mkdir()
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", mock_state_dir)
    
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._exit_at = None
    
    # Invoke command handler
    daemon._handle_ui_command("windows_lock_used", "ui")
    
    # Assertions
    assert daemon._exit_at is not None
    assert daemon._exit_at > time.monotonic()
    
    lock_state_file = mock_state_dir / "lock_state.txt"
    assert lock_state_file.exists()
    assert lock_state_file.read_text(encoding="utf-8").strip() == "UNLOCKED"
