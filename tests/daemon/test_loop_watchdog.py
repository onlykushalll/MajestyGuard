import threading

import pytest

from loop_watchdog import LoopStallGuard, should_idle_lock_while_stalled


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_heartbeat_resets_stall_timer():
    clock = Clock()
    guard = LoopStallGuard(clock)
    clock.now += 7
    assert guard.is_stalled(5.0)
    guard.beat()
    assert guard.stalled_for() == 0.0
    assert not guard.is_stalled(5.0)


def test_clock_going_backwards_never_reports_negative_stall():
    clock = Clock()
    guard = LoopStallGuard(clock)
    clock.now -= 10
    assert guard.stalled_for() == 0.0


@pytest.mark.parametrize(
    "stalled, can_lock, idle, expected",
    [
        (6.0, True, 120.0, True),     # stuck camera call, user gone: lock
        (6.0, True, 3.0, False),      # stuck camera call, user typing: never lock
        (1.0, True, 120.0, False),    # loop healthy: the loop decides
        (6.0, False, 120.0, False),   # already locked / system locked
        (6.0, True, 60.0, True),      # exactly at the timeout counts
    ],
)
def test_idle_lock_only_when_stalled_and_idle(stalled, can_lock, idle, expected):
    assert should_idle_lock_while_stalled(
        stalled_for_s=stalled, threshold_s=5.0, state_can_idle_lock=can_lock,
        idle_seconds=idle, idle_timeout_s=60.0,
    ) is expected


def test_guard_is_thread_safe():
    guard = LoopStallGuard()
    stop = threading.Event()

    def beater():
        while not stop.is_set():
            guard.beat()

    t = threading.Thread(target=beater)
    t.start()
    try:
        for _ in range(2000):
            assert guard.stalled_for() >= 0.0
    finally:
        stop.set()
        t.join()


# --- wiring in the daemon (needs the Windows-only daemon module; runs on the PC) ---------------
def _daemon(state):
    main = pytest.importorskip("main")
    d = main.MajestyGuardDaemon.__new__(main.MajestyGuardDaemon)
    d.state = main.State[state]
    d.ipc = type("Ipc", (), {"broadcast_state": lambda self, *a, **k: None})()
    d._stop = threading.Event()
    d._ui_commands = __import__("queue").Queue()
    d._absent_frames = d._stranger_frames = 0
    return main, d


def test_stalled_loop_soft_locks_an_idle_pc(monkeypatch):
    main, d = _daemon("ACTIVE")
    clock = Clock()
    d._loop_stall_guard = LoopStallGuard(clock)
    clock.now += 30
    monkeypatch.setattr(main, "get_idle_seconds", lambda: 9999.0)
    monkeypatch.setattr(main, "_atomic_write_file", lambda *a, **k: None, raising=False)
    assert d._lock_if_loop_stalled_and_idle() is True
    assert d.state is main.State.SOFT_LOCK


def test_stalled_loop_never_locks_an_active_user(monkeypatch):
    main, d = _daemon("ACTIVE")
    clock = Clock()
    d._loop_stall_guard = LoopStallGuard(clock)
    clock.now += 30
    monkeypatch.setattr(main, "get_idle_seconds", lambda: 2.0)
    assert d._lock_if_loop_stalled_and_idle() is False
    assert d.state is main.State.ACTIVE


def test_healthy_loop_is_left_alone(monkeypatch):
    main, d = _daemon("ACTIVE")
    d._loop_stall_guard = LoopStallGuard()
    monkeypatch.setattr(main, "get_idle_seconds", lambda: 9999.0)
    assert d._lock_if_loop_stalled_and_idle() is False


def test_emergency_lock_bypasses_a_stalled_loop():
    main, d = _daemon("ACTIVE")
    clock = Clock()
    d._loop_stall_guard = LoopStallGuard(clock)
    clock.now += 30
    d._queue_ui_command("emergency_lock", "pill")
    assert d.state is main.State.HOSTILE_LOCK
    assert d._ui_commands.empty()


def test_emergency_lock_is_queued_when_loop_is_healthy():
    main, d = _daemon("ACTIVE")
    d._loop_stall_guard = LoopStallGuard()
    d._queue_ui_command("emergency_lock", "pill")
    assert d.state is main.State.ACTIVE
    assert d._ui_commands.qsize() == 1


def test_one_bad_frame_is_skipped_but_repeated_failures_escalate(monkeypatch):
    main, d = _daemon("ACTIVE")
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def boom(frame, frame_no):
        calls["n"] += 1
        raise RuntimeError("onnx hiccup")

    d._tick = boom
    for _ in range(main.TICK_ERRORS_BEFORE_CRASH - 1):
        d._tick_guarded(None, 1)               # swallowed
    with pytest.raises(RuntimeError):
        d._tick_guarded(None, 1)               # persistent: crash path
    assert calls["n"] == main.TICK_ERRORS_BEFORE_CRASH


def test_good_frame_resets_the_error_streak(monkeypatch):
    main, d = _daemon("ACTIVE")
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    outcomes = iter([RuntimeError("x")] * 3 + [None] + [RuntimeError("x")] * 3)

    def tick(frame, frame_no):
        o = next(outcomes)
        if o:
            raise o

    d._tick = tick
    for _ in range(7):
        d._tick_guarded(None, 1)               # never reaches the limit of 5 in a row
    assert d._tick_error_streak == 3
