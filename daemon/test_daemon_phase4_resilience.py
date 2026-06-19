import logging
import threading

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
