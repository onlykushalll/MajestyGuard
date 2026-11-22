import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

from vdesktop_guard import DesktopFollower  # noqa: E402


class FakeManager:
    def __init__(self, on_current, ids=None, move_ok=True):
        self.on_current = on_current
        self.ids = ids or {}
        self.move_ok = move_ok
        self.moves = []

    def is_on_current(self, hwnd):
        return self.on_current

    def desktop_id_of(self, hwnd):
        return self.ids.get(hwnd)

    def move(self, hwnd, desktop):
        self.moves.append((hwnd, desktop))
        return self.move_ok


def test_overlay_already_on_current_desktop_is_left_alone():
    mgr = FakeManager(True, {10: "D2"})
    assert DesktopFollower(mgr, lambda: [10]).follow(99) is False
    assert mgr.moves == []


def test_unknown_state_does_nothing():
    mgr = FakeManager(None, {10: "D2"})
    assert DesktopFollower(mgr, lambda: [10]).follow(99) is False
    assert mgr.moves == []


def test_overlay_is_pulled_onto_the_desktop_the_user_switched_to():
    mgr = FakeManager(False, {10: "D2"})
    assert DesktopFollower(mgr, lambda: [10]).follow(99) is True
    assert mgr.moves == [(99, "D2")]


def test_falls_back_to_the_next_candidate_window():
    mgr = FakeManager(False, {30: "D2"})       # foreground (10) and tray (20) have no desktop id
    assert DesktopFollower(mgr, lambda: [10, 20, 30]).follow(99) is True
    assert mgr.moves == [(99, "D2")]


def test_never_uses_the_overlay_itself_as_the_reference_desktop():
    mgr = FakeManager(False, {99: "OLD"})
    assert DesktopFollower(mgr, lambda: [99]).follow(99) is False
    assert mgr.moves == []


def test_failed_move_reports_false():
    mgr = FakeManager(False, {10: "D2"}, move_ok=False)
    assert DesktopFollower(mgr, lambda: [10]).follow(99) is False


def test_no_window_handle_is_a_no_op():
    assert DesktopFollower(FakeManager(False), lambda: [1]).follow(0) is False
