import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

from gesture_guard import NAMES, GestureGuard  # noqa: E402


class FakeReg:
    def __init__(self, initial):
        self.values = dict(initial)
        self.fail_on_set = None

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        if self.fail_on_set == name:
            raise OSError("denied")
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


USER = {"ThreeFingerSlideEnabled": 1, "ThreeFingerTapEnabled": 2, "FourFingerTapEnabled": 4,
        "ThreeFingerUp": 2, "ThreeFingerDown": 3, "ThreeFingerLeft": 1, "ThreeFingerRight": 1}


def test_engage_zeroes_every_gesture_and_restore_returns_exact_originals(tmp_path):
    reg = FakeReg(USER)
    guard = GestureGuard(reg, tmp_path / "restore.json")

    assert guard.engage() is True
    assert all(reg.values[name] == 0 for name in NAMES)

    assert guard.restore() is True
    assert reg.values == USER                    # values that were absent are gone again, not left at 0
    assert not (tmp_path / "restore.json").exists()


def test_a_second_engage_never_overwrites_the_saved_originals(tmp_path):
    reg = FakeReg(USER)
    guard = GestureGuard(reg, tmp_path / "restore.json")
    guard.engage()
    guard.engage()
    guard.restore()
    assert reg.values == USER


def test_crash_recovery_restores_from_the_file(tmp_path):
    reg = FakeReg(USER)
    path = tmp_path / "restore.json"
    GestureGuard(reg, path).engage()             # ... and the process "crashes" here
    assert reg.values["FourFingerUp"] == 0

    fresh = GestureGuard(reg, path)
    assert fresh.recover_after_crash() is True
    assert reg.values == USER


def test_engage_after_a_crash_keeps_the_users_real_values(tmp_path):
    reg = FakeReg(USER)
    path = tmp_path / "restore.json"
    GestureGuard(reg, path).engage()             # crashed lock left everything at 0
    guard = GestureGuard(reg, path)
    guard.engage()                               # next lock must not treat the zeros as originals
    guard.restore()
    assert reg.values == USER


def test_failure_midway_rolls_back_instead_of_leaving_gestures_half_disabled(tmp_path):
    reg = FakeReg(USER)
    reg.fail_on_set = "FourFingerDown"
    guard = GestureGuard(reg, tmp_path / "restore.json")
    assert guard.engage() is False
    reg.fail_on_set = None
    assert reg.values == USER
    assert not guard.engaged


def test_nothing_to_recover_is_a_no_op(tmp_path):
    assert GestureGuard(FakeReg(USER), tmp_path / "none.json").recover_after_crash() is False


def test_failed_rollback_keeps_originals_for_recovery(tmp_path):
    reg = FakeReg(USER)
    path = tmp_path / "restore.json"
    reg.fail_on_set = "ThreeFingerUp"
    guard = GestureGuard(reg, path)
    assert not guard.engage()
    assert not guard.engaged
    assert path.is_file()
    reg.fail_on_set = None
    assert GestureGuard(reg, path).recover_after_crash()
    assert reg.values == USER


def test_invalid_existing_journal_never_gets_overwritten(tmp_path):
    reg = FakeReg(USER)
    path = tmp_path / "restore.json"
    for original in ('{', '{"FourFingerTapEnabled":"bad"}', '{}'):
        path.write_text(original, encoding="utf-8")
        guard = GestureGuard(reg, path)
        assert not guard.recover_after_crash()
        assert not guard.engage()
        assert reg.values == USER
        assert path.read_text(encoding="utf-8") == original
