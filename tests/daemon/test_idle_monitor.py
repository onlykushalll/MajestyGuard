"""Tests for 32-bit tick overflow and wraparound in idle_monitor."""
import ctypes


def test_get_idle_seconds_handles_signed_dword_overflow_and_wraparound(monkeypatch):
    """When Windows uptime exceeds 24.85 days, GetTickCount() as a signed 32-bit int
    is negative while dwTime is unsigned. get_idle_seconds() must still compute
    accurate positive elapsed seconds across the 32-bit boundary."""
    import idle_monitor

    def fake_last_input(info_ptr):
        info = ctypes.cast(info_ptr, ctypes.POINTER(idle_monitor.LASTINPUTINFO)).contents
        info.dwTime = 0xFFFFFF00  # just before 32-bit wraparound
        return 1

    monkeypatch.setattr(ctypes.windll.user32, "GetLastInputInfo", fake_last_input)
    # Simulate GetTickCount returning negative signed 32-bit int or wrapped value (0x000000EC -> +500ms)
    monkeypatch.setattr(ctypes.windll.kernel32, "GetTickCount", lambda: 0x000000EC)

    idle_s = idle_monitor.get_idle_seconds()
    assert 0.45 <= idle_s <= 0.55
