from session_monitor import SessionEvent, decode_wts_session_event


def test_wts_session_events_decode_to_daemon_events():
    assert decode_wts_session_event(0x7) == SessionEvent.SESSION_LOCK
    assert decode_wts_session_event(0x8) == SessionEvent.SESSION_UNLOCK
    assert decode_wts_session_event(0x5) == SessionEvent.SESSION_LOGON
    assert decode_wts_session_event(0x6) == SessionEvent.SESSION_LOGOFF


def test_unknown_wts_session_event_is_ignored():
    assert decode_wts_session_event(0xFFFF) is None
