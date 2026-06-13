import queue

from idle_monitor import IdleMonitor, clamp_idle_timeout, read_idle_timeout


def test_idle_timeout_env_clamps_to_safe_range():
    warnings = []

    assert read_idle_timeout({"MG_IDLE_TIMEOUT": "15"}, warn=warnings.append) == 15.0
    assert read_idle_timeout({"MG_IDLE_TIMEOUT": "900"}, warn=warnings.append) == 600.0
    assert read_idle_timeout({"MG_IDLE_TIMEOUT": "120"}, warn=warnings.append) == 120.0
    assert read_idle_timeout({"MG_IDLE_TIMEOUT": "bad"}, warn=warnings.append) == 90.0
    assert len(warnings) == 2


def test_clamp_idle_timeout_accepts_boundaries():
    assert clamp_idle_timeout(15) == 15.0
    assert clamp_idle_timeout(600) == 600.0


def test_idle_monitor_emits_after_configured_idle_time():
    events = queue.Queue()
    idle_values = iter([10.0, 29.9, 30.0, 45.0])
    monitor = IdleMonitor(
        idle_timeout_s=30.0,
        get_idle_seconds=lambda: next(idle_values),
        emit=lambda event, value: events.put((event, value)),
    )

    monitor.poll_once()
    monitor.poll_once()
    assert events.empty()

    monitor.poll_once()
    assert events.get_nowait()[0] == "IDLE_TIMEOUT"

    monitor.poll_once()
    assert events.empty()


def test_idle_monitor_resets_only_after_successful_owner_verification():
    events = queue.Queue()
    idle = {"value": 120.0}
    monitor = IdleMonitor(
        idle_timeout_s=90.0,
        get_idle_seconds=lambda: idle["value"],
        emit=lambda event, value: events.put((event, value)),
    )

    monitor.poll_once()
    assert events.get_nowait()[0] == "IDLE_TIMEOUT"

    monitor.note_overlay_input()
    monitor.poll_once()
    assert events.empty()

    monitor.note_owner_verified()
    monitor.poll_once()
    assert events.get_nowait()[0] == "IDLE_TIMEOUT"
