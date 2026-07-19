"""Regression tests for the monitor -> bounded owner-check lifecycle."""
from __future__ import annotations

import threading
from types import SimpleNamespace

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

    daemon._finish_idle_check_owner(confidence=0.91, liveness=0.84)

    assert daemon.state == State.ACTIVE
    assert daemon._exit_at > now["value"]
    assert (tmp_path / "idle_check_result.txt").read_text(encoding="utf-8").strip() == "OWNER_VERIFIED"
    assert daemon.ipc.states[-1][0] == "active"


def test_idle_check_bypasses_motion_prefilter(monkeypatch):
    daemon = _bare_daemon()
    daemon._idle_check_mode = True
    daemon.motion = SimpleNamespace(has_motion=lambda _frame: False)

    assert daemon._should_skip_idle_frame(object()) is False
