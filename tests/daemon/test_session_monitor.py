from session_monitor import SessionEvent, decode_wts_session_event


def test_wts_session_events_decode_to_daemon_events():
    assert decode_wts_session_event(0x7) == SessionEvent.SESSION_LOCK
    assert decode_wts_session_event(0x8) == SessionEvent.SESSION_UNLOCK
    assert decode_wts_session_event(0x5) == SessionEvent.SESSION_LOGON
    assert decode_wts_session_event(0x6) == SessionEvent.SESSION_LOGOFF


def test_unknown_wts_session_event_is_ignored():
    assert decode_wts_session_event(0xFFFF) is None


def test_session_monitor_rebuilds_itself_after_an_unexpected_stop():
    import threading

    from session_monitor import SessionMonitor

    mon = SessionMonitor(lambda event: None)
    waits = []

    class FastStop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

    mon._stop = FastStop()
    outcomes = iter([True, True, False])          # two crashes, then "don't retry"
    calls = []

    def fake_once():
        calls.append(1)
        return next(outcomes)

    mon._run_once = fake_once
    mon._run()
    assert len(calls) == 3
    assert waits == [1.0, 2.0]                    # exponential backoff


def test_session_monitor_does_not_restart_after_stop():
    from session_monitor import SessionMonitor

    mon = SessionMonitor(lambda event: None)
    calls = []

    def fake_once():
        calls.append(1)
        mon._stop.set()
        return True

    mon._run_once = fake_once
    mon._run()
    assert len(calls) == 1
