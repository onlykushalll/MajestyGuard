"""Tests for daemon teardown() sequence."""
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as daemon_main
from companion_ipc import FaceState
from main import MajestyGuardDaemon, State


class FakeIpc:
    def __init__(self):
        self.states = []

    def broadcast_state(self, state, **kwargs):
        self.states.append((state, kwargs))

    def stop(self):
        pass


class FakeMotion:
    def reset(self):
        pass


class FakeFaceEngine:
    def __init__(self):
        self.shutdown_called = False

    def reset_liveness(self):
        pass

    def shutdown(self):
        self.shutdown_called = True


def _bare_daemon(state=State.SOFT_LOCK):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = state
    daemon.ipc = FakeIpc()
    daemon.motion = FakeMotion()
    daemon._stop = threading.Event()
    daemon._whcdf_stop = threading.Event()
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
    daemon._is_tearing_down = False
    daemon.face_eng = FakeFaceEngine()
    daemon.service_ipc = None
    daemon.command_ipc = None
    daemon.session_monitor = None
    daemon._overlay_proc = None
    daemon._overlay_watchdog_thread = None
    return daemon


def test_teardown_is_idempotent(tmp_path):
    """Calling teardown() twice must not crash."""
    daemon = _bare_daemon()
    with patch.object(daemon_main, "_MG_STATE_DIR", tmp_path):
        with patch("sys.exit"):
            daemon.teardown()
            daemon.teardown()  # Second call — must not crash


def test_teardown_clears_face_state(tmp_path):
    """teardown() must call FaceState.clear()."""
    FaceState.set_recognized(liveness_score=0.95)
    daemon = _bare_daemon()
    with patch.object(daemon_main, "_MG_STATE_DIR", tmp_path):
        with patch("sys.exit"):
            daemon.teardown()
    authorized, _ = FaceState.is_authorized()
    assert not authorized


def test_teardown_writes_unlocked(tmp_path):
    """teardown() must write UNLOCKED to lock_state.txt."""
    daemon = _bare_daemon()
    with patch.object(daemon_main, "_MG_STATE_DIR", tmp_path):
        with patch("sys.exit"):
            daemon.teardown()
    lock_file = tmp_path / "lock_state.txt"
    assert lock_file.exists()
    assert lock_file.read_text(encoding="utf-8").strip() == "UNLOCKED"


def test_teardown_removes_daemon_pid(tmp_path):
    """teardown() must remove daemon.pid."""
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345", encoding="utf-8")
    daemon = _bare_daemon()
    with patch.object(daemon_main, "_MG_STATE_DIR", tmp_path):
        with patch("sys.exit"):
            daemon.teardown()
    assert not pid_file.exists()


def test_teardown_calls_sys_exit(tmp_path):
    """teardown() must call sys.exit(0)."""
    daemon = _bare_daemon()
    with patch.object(daemon_main, "_MG_STATE_DIR", tmp_path):
        with patch("sys.exit") as mock_exit:
            daemon.teardown()
    mock_exit.assert_called_once_with(0)
