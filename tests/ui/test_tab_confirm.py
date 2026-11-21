import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mg_tab_confirm_under_test",
    Path(__file__).resolve().parents[2] / "ui" / "tab_confirm.py",
)
tab_confirm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tab_confirm)


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def test_tab_locks_immediately_when_the_camera_is_healthy():
    gate = tab_confirm.TabConfirmGate(clock=Clock())

    assert gate.press(confirmation_required=False) == tab_confirm.ACTION_LOCK
    assert gate.armed is False


def test_camera_fault_needs_a_second_tab_to_confirm():
    clock = Clock()
    gate = tab_confirm.TabConfirmGate(window_s=6.0, clock=clock)

    assert gate.press(True) == tab_confirm.ACTION_PROMPT
    assert gate.armed is True
    clock.now += 2.0
    assert gate.press(True) == tab_confirm.ACTION_LOCK
    assert gate.armed is False


def test_an_unconfirmed_press_expires_and_must_be_repeated():
    clock = Clock()
    gate = tab_confirm.TabConfirmGate(window_s=6.0, clock=clock)

    assert gate.press(True) == tab_confirm.ACTION_PROMPT
    clock.now += 6.5
    assert gate.armed is False
    assert gate.press(True) == tab_confirm.ACTION_PROMPT


def test_a_confirmed_lock_does_not_leave_the_gate_armed():
    clock = Clock()
    gate = tab_confirm.TabConfirmGate(clock=clock)

    gate.press(True)
    assert gate.press(True) == tab_confirm.ACTION_LOCK
    assert gate.press(True) == tab_confirm.ACTION_PROMPT


def test_reset_disarms():
    gate = tab_confirm.TabConfirmGate(clock=Clock())
    gate.press(True)

    gate.reset()

    assert gate.armed is False
