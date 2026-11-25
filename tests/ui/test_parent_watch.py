from types import SimpleNamespace

import pytest

import parent_watch
from parent_watch import OrphanGuard, configured_daemon_pid


@pytest.mark.parametrize(
    ("wait_result", "expected_alive"),
    [(0, False), (0x102, True), (-1, True)],
    ids=["signaled", "running", "wait-failed"],
)
def test_process_probe_only_reports_exit_for_a_signaled_handle(monkeypatch, wait_result, expected_alive):
    closed = []
    kernel32 = SimpleNamespace(
        OpenProcess=lambda *_: 42,
        WaitForSingleObject=lambda *_: wait_result,
        CloseHandle=lambda handle: closed.append(handle.value),
    )
    monkeypatch.setattr(parent_watch.ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert parent_watch.process_alive(1234) is expected_alive
    assert closed == [42]


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


def test_quit_flag_is_ignored_while_lock_overlay_is_visible(tmp_path):
    from parent_watch import quit_requested
    flag = tmp_path / "ui_quit.flag"
    flag.write_text("quit\n")
    assert quit_requested(str(flag), overlay_visible=True) is False
    assert not flag.exists()                  # discarded, so it cannot fire after the overlay hides
    assert quit_requested(str(flag), overlay_visible=False) is False
