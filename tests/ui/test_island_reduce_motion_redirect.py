"""verifying_lock -> social_lock/hostile_lock is redirected through a
"stranger" shake, with the real lock state parked in _deferred_lock_state
until the shake ends. With MG_UI_REDUCE_MOTION=1 no shake ever runs, so
the park is never consumed: the pill sticks on "stranger" and the target
is orphaned for a later unrelated shake to consume.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import island  # noqa: E402
from states import get_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def _reject_after_verify(widget, rejected_to: str) -> None:
    widget.apply_state(get_state("verifying_lock"))
    widget._verify_start_time -= 2.0  # past the 650ms minimum scan time
    widget.apply_state(get_state(rejected_to))


@pytest.mark.parametrize("rejected_to", ["social_lock", "hostile_lock"])
def test_reduce_motion_rejection_lands_on_the_lock_state(qapp, monkeypatch, rejected_to):
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    widget = island.IslandWidget()

    _reject_after_verify(widget, rejected_to)

    assert widget._state.name == rejected_to
    assert widget._deferred_lock_state is None, "no shake will run, so nothing may stay parked"


def test_normal_motion_rejection_still_shakes_first(qapp, monkeypatch):
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    widget = island.IslandWidget()

    _reject_after_verify(widget, "social_lock")

    assert widget._state.name == "stranger"
    assert widget._anim_shake_active is True
    assert widget._deferred_lock_state is not None
    assert widget._deferred_lock_state.name == "social_lock"
