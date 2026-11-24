"""Regression tests for the monitor -> bounded owner-check lifecycle."""
from __future__ import annotations

import threading
import sys
from pathlib import Path
from types import SimpleNamespace

DAEMON = Path(__file__).resolve().parents[2] / "daemon"
sys.path.insert(0, str(DAEMON))

import main as daemon_main
from main import MajestyGuardDaemon, State


class FakeIpc:
    def __init__(self):
        self.states = []

    def broadcast_state(self, state, **kwargs):
        self.states.append((state, kwargs))


class FakeMotion:
    def reset(self):
        pass


def _bare_daemon() -> MajestyGuardDaemon:
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = State.IDLE
    daemon.ipc = FakeIpc()
    daemon.motion = FakeMotion()
    daemon._stop = threading.Event()
    daemon._absent_frames = 0
    daemon._stranger_frames = 0
    daemon._active_reacquire_grace_frames = 0
    daemon._owner_continuity_grace_frames = 0
    daemon._scanning_owner_ambiguity_grace_frames = 0
    daemon._scanning_owner_candidate_frames = 0
    daemon._soft_lock_verify_until = 0.0
    daemon._soft_lock_verification_started_at = 0.0
    daemon._soft_lock_owner_candidate_frames = 0
    daemon._soft_lock_fast_pass_frames = 0
    daemon._background_processes_restricted = False
    daemon._input_idle_soft_lock_armed = True
    daemon._cap = None
    daemon._idle_check_mode = True
    daemon._idle_check_deadline = 0.0
    return daemon


def test_idle_owner_probe_is_short_and_separate_from_locked_verification():
    assert daemon_main.IDLE_OWNER_PROBE_SECONDS == 14.0
    assert daemon_main.SOFT_LOCK_VERIFY_WINDOW_SECONDS == 15.0


def test_idle_check_timeout_enters_passive_overlay(monkeypatch, tmp_path):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon()
    daemon._idle_check_deadline = 99.9

    assert daemon._expire_idle_check_if_needed() is True
    assert daemon.state == State.SOFT_LOCK
    assert daemon.ipc.states[-1] == ("locked_passive", {"detail": "idle_check_timeout"})
    assert (tmp_path / "lock_state.txt").read_text(encoding="utf-8").strip() == "LOCKED"


def test_idle_check_owner_success_exits_back_to_monitor(monkeypatch, tmp_path):
    now = {"value": 100.0}
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon()

    camera_released = []
    daemon._cap = SimpleNamespace(release=lambda: camera_released.append(True))
    daemon._finish_idle_check_owner(confidence=0.91, liveness=0.84)

    assert camera_released == [True] and daemon._cap is None
    assert daemon.state == State.ACTIVE
    assert daemon._exit_at > now["value"]
    assert (tmp_path / "idle_check_result.txt").read_text(encoding="utf-8").strip() == "OWNER_VERIFIED"
    assert daemon.ipc.states[-1][0] == "active"


def test_idle_probe_never_announces_scanning_after_camera_fault(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon()
    daemon._launch_ui = lambda: None
    def unavailable():
        daemon._enter_soft_lock("camera_unavailable")
        daemon.ipc.broadcast_state("camera_fault")
        return False
    daemon._open_camera = unavailable
    daemon._start_idle_probe()
    assert daemon._idle_check_deadline == 0
    assert (tmp_path / "idle_check_result.txt").read_text().strip() == "LOCKED"
    assert [s for s, _ in daemon.ipc.states] == ["idle_detected", "locked_passive", "camera_fault"]


def test_monitor_handoff_dwell_does_not_relock_or_reopen_camera(monkeypatch):
    from unittest.mock import Mock
    daemon = _bare_daemon()
    daemon.state = State.ACTIVE
    daemon._exit_at = 10
    monkeypatch.setattr(daemon_main.time, "monotonic", lambda: 5)
    daemon._drain_session_events = Mock()
    daemon._drain_ui_commands = Mock()
    daemon._stop_after_time_limit = lambda _: False
    daemon._stop.wait = lambda _: daemon._stop.set()
    daemon._open_camera = Mock()
    daemon._maybe_enter_soft_lock_for_input_idle = Mock()
    daemon._run_loop()
    daemon._open_camera.assert_not_called()
    daemon._maybe_enter_soft_lock_for_input_idle.assert_not_called()


def test_idle_check_bypasses_motion_prefilter(monkeypatch):
    daemon = _bare_daemon()
    daemon._idle_check_mode = True
    daemon.motion = SimpleNamespace(has_motion=lambda _frame: False)

    assert daemon._should_skip_idle_frame(object()) is False


def test_idle_check_phase_publishes_monitor_lifecycle_boundary(monkeypatch, tmp_path):
    """The monitor may only cancel explicitly pre-camera phases, so the
    full daemon must publish a durable phase before UI/camera startup."""
    daemon = _bare_daemon()
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)

    daemon._set_idle_check_phase("STARTING_UI")

    assert (tmp_path / "idle_check_result.txt").read_text(encoding="utf-8") == "STARTING_UI\n"

def test_persistent_idle_loop_parks_camera_and_stays_alive_after_owner_verify(monkeypatch, tmp_path):
    """Under Control Center supervision (MG_PERSISTENT_IDLE_LOOP=1), verifying the owner
    or clearing soft-lock must release the camera and return to IDLE without setting _exit_at."""
    monkeypatch.setattr(daemon_main, "_MG_STATE_DIR", tmp_path)
    daemon = _bare_daemon()
    daemon._persistent_idle_loop = True
    released = []
    daemon._release_camera = lambda: released.append(True)

    daemon._finish_idle_check_owner(confidence=0.92, liveness=0.85)

    assert not hasattr(daemon, "_exit_at") or getattr(daemon, "_exit_at", None) is None
    assert daemon.state == State.IDLE
    assert released == [True]
    assert daemon.ipc.states[-1][0] == "active"

    daemon.state = State.SOFT_LOCK
    daemon._clear_soft_lock(confidence=0.93, liveness=0.86)

    assert not hasattr(daemon, "_exit_at") or getattr(daemon, "_exit_at", None) is None
    assert daemon.state == State.IDLE
    assert len(released) == 2
