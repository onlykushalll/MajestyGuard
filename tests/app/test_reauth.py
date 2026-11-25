from daemon import enroll_reauth as reauth


def test_logon_requires_same_windows_sid_and_releases_handles(monkeypatch):
    import pytest
    security = pytest.importorskip("win32security")
    from unittest.mock import Mock

    login, current = Mock(), Mock()
    monkeypatch.setattr(security, "LogonUser", lambda *args: login)
    monkeypatch.setattr(security, "OpenProcessToken", lambda *args: current)
    monkeypatch.setattr(security, "ConvertSidToStringSid", str)
    ids = {current: "current", login: "other"}
    monkeypatch.setattr(security, "GetTokenInformation", lambda token, _: (ids[token], 0))
    assert not reauth._logon("tester", ".", "test-only")
    ids[login] = ids[current]
    assert reauth._logon("tester", ".", "test-only")
    assert login.Close.call_count == current.Close.call_count == 2


def test_cancelled_dialog_is_refused():
    assert reauth.authenticate(lambda: None, lambda u, d, p: True) is False


def test_wrong_password_is_refused():
    assert reauth.authenticate(lambda: ("kio", "PC", "nope"), lambda u, d, p: False) is False


def test_correct_password_is_accepted_and_passed_through_unchanged():
    seen = []
    assert reauth.authenticate(lambda: ("kio", "PC", "hunter2"),
                               lambda u, d, p: seen.append((u, d, p)) or True) is True
    assert seen == [("kio", "PC", "hunter2")]


def test_blank_credentials_never_reach_windows():
    called = []
    assert reauth.authenticate(lambda: ("kio", "PC", ""), lambda *a: called.append(a) or True) is False
    assert reauth.authenticate(lambda: ("", "PC", "x"), lambda *a: called.append(a) or True) is False
    assert called == []


def test_an_error_while_checking_means_refused():
    def boom(u, d, p):
        raise OSError("lsass unavailable")

    assert reauth.authenticate(lambda: ("kio", "", "pw"), boom) is False


def test_off_windows_it_is_always_refused(monkeypatch):
    monkeypatch.setattr(reauth.sys, "platform", "linux")
    assert reauth.verify_windows_password() is False
