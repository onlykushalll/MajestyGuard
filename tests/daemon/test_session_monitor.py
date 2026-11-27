from session_monitor import SessionEvent, SessionMonitor, decode_wts_session_event


def test_wts_session_events_decode_to_daemon_events():
    assert decode_wts_session_event(0x7) == SessionEvent.SESSION_LOCK
    assert decode_wts_session_event(0x8) == SessionEvent.SESSION_UNLOCK
    assert decode_wts_session_event(0x5) == SessionEvent.SESSION_LOGON
    assert decode_wts_session_event(0x6) == SessionEvent.SESSION_LOGOFF


def test_unknown_wts_session_event_is_ignored():
    assert decode_wts_session_event(0xFFFF) is None


def test_listener_retries_with_backoff_and_never_reopens_after_stop(monkeypatch):
    monitor = SessionMonitor(lambda event: None)
    waits = []
    attempts = []

    def register_once():
        attempts.append(True)
        if len(attempts) == 3:
            monitor._stop.set()
        return True

    monkeypatch.setattr(monitor, "_run_once", register_once, raising=False)
    monkeypatch.setattr(monitor._stop, "wait", lambda delay: waits.append(delay) or False)
    monitor._run()
    assert len(attempts) == 3
    assert waits == [1.0, 2.0]
    assert not monitor.available
