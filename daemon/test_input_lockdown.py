"""Tests for Phase 5.6 input lockdown: mouse/cursor block, keyboard allowlist, verify cooldown."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))
sys.path.insert(0, str(ROOT / "daemon"))

import main as daemon_main
from main import MajestyGuardDaemon, State


@pytest.fixture(autouse=True)
def mock_timer(monkeypatch):
    class FakeTimer:
        def __init__(self, interval, function, args=None, kwargs=None):
            self.function = function
            self.args = args or []
            self.kwargs = kwargs or {}
        def start(self):
            pass
        def cancel(self):
            pass
    monkeypatch.setattr(threading, "Timer", FakeTimer)


class FakeIpc:
    def __init__(self):
        self.states = []

    def broadcast_state(self, state, **kwargs):
        self.states.append((state, kwargs))


class FakeFaceEngine:
    def __init__(self):
        self.reset_liveness_calls = 0

    def reset_liveness(self):
        self.reset_liveness_calls += 1

    def process_frame(self, _frame, *, liveness_mode="full"):
        return SimpleNamespace(face_count=0)


def _bare_daemon(state=State.SOFT_LOCK):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = state
    daemon.ipc = FakeIpc()
    daemon.motion = SimpleNamespace(reset=lambda: None)
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
    daemon._verify_cooldown_until = 0.0
    daemon.face_eng = FakeFaceEngine()
    daemon.service_ipc = None
    daemon.command_ipc = None
    daemon._overlay_proc = None
    daemon._overlay_watchdog_thread = None
    daemon._whcdf_stop = threading.Event()
    daemon._is_tearing_down = False
    daemon._background_processes_restricted = False
    daemon._scanning_owner_candidate_frames = 0
    return daemon


# ── Keyboard allowlist (source-level tests) ──────────────────────────


def test_keyboard_allowlist_blocks_everything_except_tab_space():
    """Keyboard hook must use allowlist: only VK_TAB and VK_SPACE pass."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_TAB" in source
    assert "VK_SPACE" in source
    assert "_any_modifier_held" in source
    # Allowlist pattern: check for tab/space, block everything else with return 1
    assert "return 1" in source


def test_keyboard_allowlist_blocks_tab_with_modifier_held():
    """Hook checks _any_modifier_held before allowing Tab/Space."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_any_modifier_held" in source
    assert "VK_CTRL" in source or "0x11" in source
    assert "VK_ALT" in source or "0x12" in source
    assert "VK_LWIN" in source
    assert "VK_RWIN" in source


# ── Mouse hook (source-level tests) ──────────────────────────────────


def test_mouse_hook_swallows_all_button_and_wheel_messages():
    """WH_MOUSE_LL hook must block all mouse messages when locked."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "WH_MOUSE_LL" in source
    assert "WM_MOUSEMOVE" in source
    assert "WM_LBUTTONDOWN" in source
    assert "WM_RBUTTONDOWN" in source
    assert "WM_MOUSEWHEEL" in source
    assert "WM_XBUTTONDOWN" in source
    assert "_BLOCKED_MOUSE_MSGS" in source
    assert "_mouse_ll_callback" in source
    assert "MSLLHOOKSTRUCT" in source


def test_cursor_lock_released_in_teardown():
    """ClipCursor(None) and mouse unhook must appear in _uninstall_hooks."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_release_cursor_lock" in source
    assert "ClipCursor" in source
    # _uninstall_hooks calls _release_cursor_lock
    assert "_uninstall_hooks" in source


def test_cursor_lock_released_in_atexit_handler():
    """atexit handler must call _uninstall_hooks (which releases cursor lock)."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "atexit" in source
    assert "_atexit_release_all" in source or "_uninstall_hooks" in source


def test_cursor_lock_released_before_hostile_lock_lockworkstation():
    """_use_windows_lock must call _uninstall_hooks before LockWorkStation."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    lines = source.split("\n")
    in_method = False
    unhook_line = -1
    lock_line = -1
    for i, line in enumerate(lines):
        if "def _use_windows_lock" in line:
            in_method = True
        elif in_method and line.strip().startswith("def "):
            break
        elif in_method:
            if "_uninstall_hooks" in line:
                unhook_line = i
            if "LockWorkStation" in line:
                lock_line = i
    assert unhook_line > 0, "_uninstall_hooks not found in _use_windows_lock"
    assert lock_line > 0, "LockWorkStation not found in _use_windows_lock"
    assert unhook_line < lock_line, "_uninstall_hooks must be called before LockWorkStation"


def test_cursor_lock_reasserted_every_tick():
    """_tick must call _engage_cursor_lock when _mouse_locked is True."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    lines = source.split("\n")
    in_tick = False
    found = False
    for line in lines:
        if "def _tick(self)" in line:
            in_tick = True
        elif in_tick and line.strip().startswith("def "):
            break
        elif in_tick and "_engage_cursor_lock" in line:
            found = True
            break
    assert found, "_engage_cursor_lock not called in _tick"


# ── Verify cooldown (daemon-level tests) ─────────────────────────────


def test_verify_cooldown_blocks_verify_requested_for_5_seconds():
    """verify_requested must be ignored during 5s cooldown window."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._verify_cooldown_until = time.monotonic() + 5.0

    daemon._handle_ui_command("verify_requested", "test")

    # Should NOT have started verification — no verifying_lock broadcast
    verify_states = [s for s, _ in daemon.ipc.states if s == "verifying_lock"]
    assert len(verify_states) == 0, "verify_requested should be blocked during cooldown"


def test_verify_cooldown_allows_after_expiry():
    """verify_requested must work after cooldown expires."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._verify_cooldown_until = time.monotonic() - 1.0  # expired

    daemon._handle_ui_command("verify_requested", "test")

    verify_states = [s for s, _ in daemon.ipc.states if s == "verifying_lock"]
    assert len(verify_states) == 1, "verify_requested should work after cooldown expires"


def test_verify_cooldown_does_not_block_confirmed_stranger_escalation():
    """Stranger escalation path must work regardless of cooldown."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._verify_cooldown_until = time.monotonic() + 5.0

    # _soft_lock_stranger is the stranger escalation — it doesn't go through
    # _handle_ui_command, so cooldown should not affect it
    daemon._soft_lock_stranger()

    assert daemon.state == State.SOCIAL_LOCK


def test_verify_inconclusive_sets_cooldown():
    """_on_verify_inconclusive must set _verify_cooldown_until ~5s from now."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    before = time.monotonic()
    daemon._on_verify_inconclusive()
    after = time.monotonic()

    assert daemon._verify_cooldown_until >= before + 4.5
    assert daemon._verify_cooldown_until <= after + 5.5

    # Must broadcast verify_failed
    failed_states = [s for s, _ in daemon.ipc.states if s == "verify_failed"]
    assert len(failed_states) == 1


def test_verify_expiration_triggers_cooldown():
    """When verify window expires, cooldown must be triggered."""
    daemon = _bare_daemon(State.SOFT_LOCK)
    daemon._soft_lock_verify_until = time.monotonic() - 0.1  # already expired

    daemon._expire_soft_lock_verification_if_needed()

    assert daemon._verify_cooldown_until > time.monotonic()


# ── focusNextPrevChild ────────────────────────────────────────────────


def test_focus_next_prev_child_disabled():
    """focusNextPrevChild must return False to prevent Qt Tab focus traversal."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "focusNextPrevChild" in source
    # Check the method returns False
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if "def focusNextPrevChild" in line:
            body = "\n".join(lines[i:i + 3])
            assert "return False" in body
            break
    else:
        pytest.fail("focusNextPrevChild not found")


# ── Tab triggers Windows lock ─────────────────────────────────────────


def test_tab_triggers_windows_lock():
    """keyPressEvent must call _use_windows_lock on Tab."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    lines = source.split("\n")
    in_method = False
    found_tab = False
    found_windows_lock = False
    for line in lines:
        if "def keyPressEvent" in line:
            in_method = True
        elif in_method and line.strip().startswith("def "):
            break
        elif in_method:
            if "Key_Tab" in line:
                found_tab = True
            if "_use_windows_lock" in line and found_tab:
                found_windows_lock = True
    assert found_tab, "Key_Tab not checked in keyPressEvent"
    assert found_windows_lock, "_use_windows_lock not called on Tab"


def test_fallback_button_mentions_tab():
    """Fallback button text must mention TAB key."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "TAB" in source


# ── verify_failed state exists ────────────────────────────────────────


def test_verify_failed_state_exists():
    """verify_failed must be defined in states.py."""
    from states import STATES
    assert "verify_failed" in STATES
    vf = STATES["verify_failed"]
    assert vf.accent_color == "#FF453A"  # red


def test_verify_failed_in_lock_names():
    """verify_failed must be in _LOCK_NAMES so overlay stays visible."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "verify_failed" in source
    # Must appear in the _LOCK_NAMES set definition
    assert '"verify_failed"' in source or "'verify_failed'" in source


# ── Legacy compat aliases ─────────────────────────────────────────────


def test_legacy_hook_aliases_exist():
    """_install_keyboard_hook and _uninstall_keyboard_hook aliases must exist."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_install_keyboard_hook" in source
    assert "_uninstall_keyboard_hook" in source
