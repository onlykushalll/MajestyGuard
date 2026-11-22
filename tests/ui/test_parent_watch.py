import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

from parent_watch import OrphanGuard, configured_daemon_pid  # noqa: E402


def test_standalone_ui_never_exits():
    guard = OrphanGuard(None, alive=lambda pid: False)
    assert not any(guard.should_exit(False) for _ in range(5))


def test_exits_after_consecutive_confirmations_when_daemon_is_gone_and_no_lock():
    guard = OrphanGuard(1234, alive=lambda pid: False, strikes_needed=2)
    assert guard.should_exit(False) is False
    assert guard.should_exit(False) is True


def test_a_live_daemon_resets_the_strikes():
    state = {"alive": False}
    guard = OrphanGuard(1234, alive=lambda pid: state["alive"], strikes_needed=2)
    assert guard.should_exit(False) is False
    state["alive"] = True
    assert guard.should_exit(False) is False
    state["alive"] = False
    assert guard.should_exit(False) is False  # counting restarted
    assert guard.should_exit(False) is True


def test_never_abandons_a_visible_lock_overlay():
    guard = OrphanGuard(1234, alive=lambda pid: False, strikes_needed=1)
    assert not any(guard.should_exit(True) for _ in range(10))
    assert guard.should_exit(False) is True  # once the overlay is gone it may leave


def test_daemon_pid_comes_from_the_environment():
    assert configured_daemon_pid({"MG_DAEMON_PID": "4242"}) == 4242
    assert configured_daemon_pid({"MG_DAEMON_PID": "abc"}) is None
    assert configured_daemon_pid({"MG_DAEMON_PID": "0"}) is None
    assert configured_daemon_pid({}) is None


def test_quit_flag_is_consumed_once(tmp_path):
    from parent_watch import QUIT_FLAG_ENV, quit_flag_path, quit_requested
    flag = tmp_path / "ui_quit.flag"
    assert quit_flag_path({QUIT_FLAG_ENV: str(flag)}) == str(flag)
    assert quit_flag_path({}) is None
    assert quit_requested(str(flag)) is False
    flag.write_text("quit")
    assert quit_requested(str(flag)) is True
    assert not flag.exists()
    assert quit_requested(str(flag)) is False
    assert quit_requested(None) is False
