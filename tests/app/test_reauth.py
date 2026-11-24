from app import reauth


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
