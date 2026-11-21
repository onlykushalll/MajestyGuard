import logging
import threading
from unittest.mock import MagicMock

import pytest

import main as daemon_main
from companion_ipc import FaceState
from main import MajestyGuardDaemon, State
from session_monitor import SessionEvent


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


class FakeCap:
    def __init__(self, opened):
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def set(self, *_args):
        return True

    def get(self, *_args):
        return 0

    def release(self):
        self.released = True


def _bare_daemon(state=State.IDLE):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = state
    daemon.ipc = FakeIpc()
    daemon.motion = FakeMotion()
    daemon._stop = threading.Event()
    daemon._absent_frames = 3
    daemon._stranger_frames = 2
    daemon._active_reacquire_grace_frames = 1
    daemon._owner_continuity_grace_frames = 1
    daemon._scanning_owner_ambiguity_grace_frames = 1
    daemon._cap = None
    daemon._camera_read_failures = 0
    daemon._last_camera_retry_at = 0.0
    daemon.service_ipc = None
    daemon.session_monitor = None
    return daemon


def test_session_lock_enters_system_locked_and_releases_camera():
    daemon = _bare_daemon(State.ACTIVE)
    cap = FakeCap(opened=True)
    daemon._cap = cap
    FaceState.set_recognized(liveness_score=0.93)

    daemon._handle_session_event(SessionEvent.SESSION_LOCK)

    assert daemon.state == State.SYSTEM_LOCKED
    assert cap.released is True
    assert daemon._cap is None
    assert daemon.ipc.states[-1][0] == "locked"
    assert FaceState.is_authorized()[0] is False


def test_session_unlock_returns_to_idle_without_real_lock_attempt(monkeypatch, tmp_path):
    daemon = _bare_daemon(State.SYSTEM_LOCKED)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)

    daemon._handle_session_event(SessionEvent.SESSION_UNLOCK)

    assert daemon.state == State.IDLE
    assert daemon.motion.reset_calls == 1
    assert daemon.ipc.states[-1][0] == "idle"


def test_camera_open_failure_transitions_to_camera_unavailable(monkeypatch):
    attempts = []

    def fake_video_capture(*_args):
        attempts.append(1)
        return FakeCap(opened=False)

    monkeypatch.setattr(daemon_main.cv2, "VideoCapture", fake_video_capture)
    daemon = _bare_daemon(State.IDLE)

    opened = daemon._open_camera(max_attempts=2, retry_delay_s=0)

    assert opened is False
    assert len(attempts) == 4
    # Camera trouble no longer forces a Windows lock: wait, heal, soft-lock on idle.
    assert daemon.state == State.CAMERA_UNAVAILABLE
    assert daemon.ipc.states[-1][0] == "idle"



def test_camera_open_success_recovers_from_camera_unavailable(monkeypatch):
    monkeypatch.setattr(daemon_main.cv2, "VideoCapture", lambda *_args: FakeCap(opened=True))
    daemon = _bare_daemon(State.CAMERA_UNAVAILABLE)

    opened = daemon._open_camera(max_attempts=1, retry_delay_s=0)

    assert opened is True
    assert daemon.state == State.IDLE
    assert daemon.ipc.states[-1][0] == "idle"


def test_camera_unavailable_requires_three_consecutive_read_failures():
    daemon = _bare_daemon(State.ACTIVE)
    cap = FakeCap(opened=True)
    daemon._cap = cap

    daemon._handle_camera_read_failure()
    daemon._handle_camera_read_failure()

    assert daemon.state == State.ACTIVE
    assert cap.released is False

    daemon._handle_camera_read_failure()

    assert daemon.state == State.CAMERA_UNAVAILABLE
    assert cap.released is True
    assert daemon._cap is None
    assert daemon.ipc.states[-1][0] == "idle"


def test_daemon_logging_uses_rotating_file_handler():
    root_handlers = logging.getLogger().handlers

    assert any(
        handler.__class__.__name__ == "RotatingFileHandler"
        for handler in root_handlers
    )


def test_normal_start_launches_owned_ui_after_state_pipe(monkeypatch):
    daemon = MajestyGuardDaemon()
    events = []

    daemon._write_daemon_pid = lambda: None
    daemon.ipc = MagicMock()
    daemon.ipc.start.side_effect = lambda: events.append("pipe")
    daemon.session_monitor = MagicMock()
    daemon._start_overlay_watchdog = lambda: None
    daemon._load_cv_engine = lambda: None
    daemon._warmup_inference = lambda: None
    daemon._should_open_camera_on_start = lambda: False
    daemon._run_loop = lambda: None
    daemon._shutdown = lambda: None
    daemon._launch_ui = lambda: events.append("ui")
    monkeypatch.setattr(daemon_main, "WHCDF_IPC_ENABLED", False)
    monkeypatch.setattr(daemon_main, "SERVICE_IPC_ENABLED", False)
    monkeypatch.setattr(
        daemon_main,
        "CMDServer",
        lambda *_args, **_kwargs: type("FakeCommandServer", (), {"start": lambda self: events.append("command")})(),
    )

    daemon.start()

    assert events[:3] == ["pipe", "ui", "command"]


def test_launch_ui_prefers_explicit_packaged_ui_executable(monkeypatch, tmp_path):
    daemon = _bare_daemon()
    daemon._overlay_proc = None
    packaged_ui = tmp_path / "MajestyGuardUI.exe"
    packaged_ui.write_bytes(b"ui")
    started = {}

    monkeypatch.setattr(daemon_main, "_ROOT_DIR", tmp_path)
    monkeypatch.setenv("MG_UI_EXECUTABLE", str(packaged_ui))
    monkeypatch.setattr(
        daemon_main.subprocess,
        "Popen",
        lambda command, **kwargs: started.update(command=command, kwargs=kwargs) or type("Process", (), {"poll": lambda self: None})(),
    )

    daemon._launch_ui()

    assert started["command"] == [str(packaged_ui)]


# ---------------------------------------------------------------------------
# Camera self-healing: a camera fault never forces a Windows lock.
# ---------------------------------------------------------------------------

def _forbid_windows_lock(monkeypatch):
    def boom():
        raise AssertionError("camera trouble must not auto-lock Windows")

    monkeypatch.setattr(daemon_main, "lock_workstation", boom)


def _failing_camera(monkeypatch):
    monkeypatch.setattr(daemon_main.cv2, "VideoCapture", lambda *_a: FakeCap(opened=False))


def test_open_failure_while_unlocked_waits_without_locking_windows(monkeypatch, tmp_path):
    _forbid_windows_lock(monkeypatch)
    _failing_camera(monkeypatch)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.IDLE)

    assert daemon._open_camera(max_attempts=1, retry_delay_s=0) is False

    assert daemon.state == State.CAMERA_UNAVAILABLE
    assert daemon._camera_fault_active is True
    assert daemon.ipc.states[-1][0] == "idle"
    assert (tmp_path / "camera_health.json").is_file()


def test_open_failure_while_locked_keeps_overlay_and_reports_the_fault(monkeypatch, tmp_path):
    _forbid_windows_lock(monkeypatch)
    _failing_camera(monkeypatch)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._soft_lock_verify_until = 9999.0

    assert daemon._open_camera(max_attempts=1, retry_delay_s=0) is False

    assert daemon.state == State.SOFT_LOCK
    assert daemon._soft_lock_verify_until == 0.0
    state, kwargs = daemon.ipc.states[-1]
    assert state == "camera_fault"
    assert "Camera unavailable" in kwargs["detail"]


def test_idle_probe_open_failure_soft_locks_with_the_overlay_not_windows(monkeypatch, tmp_path):
    _forbid_windows_lock(monkeypatch)
    _failing_camera(monkeypatch)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.IDLE)
    daemon._idle_check_mode = True

    daemon._open_camera(max_attempts=1, retry_delay_s=0)

    assert daemon.state == State.SOFT_LOCK
    assert daemon.ipc.states[-1][0] == "camera_fault"
    assert (tmp_path / "lock_state.txt").read_text(encoding="utf-8").strip() == "LOCKED"


def test_read_failures_while_locked_hold_the_lock_instead_of_locking_windows(monkeypatch, tmp_path):
    _forbid_windows_lock(monkeypatch)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._cap = FakeCap(opened=True)

    for _ in range(daemon_main.CAMERA_READ_FAILURES_BEFORE_UNAVAILABLE):
        daemon._handle_camera_read_failure()

    assert daemon.state == State.SOFT_LOCK
    assert daemon._cap is None
    assert daemon.ipc.states[-1][0] == "camera_fault"
    assert "not responding" in daemon.ipc.states[-1][1]["detail"]


def test_passive_lock_shows_the_fault_and_waits_for_the_backoff(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon_main.time, "sleep", lambda _s: None)
    daemon = _bare_daemon(State.SOFT_LOCK)
    health = daemon._camera_health_tracker()
    health.record_open_failure()
    daemon._camera_fault_active = True
    opens = []
    monkeypatch.setattr(daemon, "_open_camera", lambda **kw: opens.append(kw) or False)

    daemon._handle_passive_soft_lock(loop_start=0.0)

    assert opens == []  # backoff has not elapsed
    assert daemon.ipc.states[-1][0] == "camera_fault"


def test_passive_lock_heals_the_camera_once_the_backoff_elapses(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon_main.time, "sleep", lambda _s: None)
    monkeypatch.setattr(daemon_main.cv2, "VideoCapture", lambda *_a: FakeCap(opened=True))
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._camera_health_tracker().record_open_failure()
    daemon._camera_health_tracker().request_retry_now()
    daemon._camera_fault_active = True

    daemon._handle_passive_soft_lock(loop_start=0.0)

    assert daemon._camera_fault_active is False
    assert daemon.ipc.states[-1][0] == "locked_passive"
    assert daemon._cap is None  # passive lock stays camera-cold after the probe


def test_space_while_faulted_retries_the_camera_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._camera_health_tracker().record_open_failure()
    daemon._camera_fault_active = True
    attempts = []
    monkeypatch.setattr(daemon, "_open_camera", lambda **kw: attempts.append(kw) or False)

    daemon._handle_ui_command("verify_requested", "overlay_key")

    assert len(attempts) == 1  # backoff was cleared by the explicit request
    assert daemon._is_soft_lock_verifying() is False


def test_camera_unavailable_soft_locks_when_the_user_goes_idle(monkeypatch, tmp_path):
    _forbid_windows_lock(monkeypatch)
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon_main, "get_idle_seconds", lambda: 500.0)
    daemon = _bare_daemon(State.CAMERA_UNAVAILABLE)
    daemon._camera_fault_active = True
    monkeypatch.setattr(daemon, "_current_idle_timeout", lambda: 60.0)

    assert daemon._maybe_enter_soft_lock_for_input_idle() is True

    assert daemon.state == State.SOFT_LOCK
    assert daemon._camera_fault_active is True  # overlay keeps showing the fault


def test_successful_open_clears_the_fault_flag_and_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon_main.cv2, "VideoCapture", lambda *_a: FakeCap(opened=True))
    daemon = _bare_daemon(State.CAMERA_UNAVAILABLE)
    daemon._camera_health_tracker().record_open_failure()
    daemon._camera_fault_active = True

    assert daemon._open_camera(max_attempts=1, retry_delay_s=0) is True

    assert daemon._camera_fault_active is False
    assert daemon._camera_health_tracker().total_recoveries == 1
    assert '"fault":"none"' in (tmp_path / "camera_health.json").read_text(encoding="utf-8")


def test_frozen_frames_trigger_a_reopen(monkeypatch, tmp_path):
    import numpy as np

    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.ACTIVE)
    daemon._cap = FakeCap(opened=True)
    frame = np.full((480, 640, 3), 90, dtype=np.uint8)

    results = [daemon._observe_frame_health(frame) for _ in range(80)]

    assert any(results)
    assert daemon._camera_fault_active is True
    assert daemon.state == State.CAMERA_UNAVAILABLE
    assert daemon._cap is None


def test_changing_frames_never_trigger_a_reopen(monkeypatch, tmp_path):
    import numpy as np

    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon(State.ACTIVE)
    rng = np.random.default_rng(1)

    for _ in range(80):
        frame = rng.integers(10, 200, size=(480, 640, 3), dtype=np.uint8)
        assert daemon._observe_frame_health(frame) is False

    assert getattr(daemon, "_camera_fault_active", False) is False
