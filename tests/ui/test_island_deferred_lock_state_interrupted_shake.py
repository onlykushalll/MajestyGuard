"""Second regression test from the same debugging pass as
test_island_deferred_lock_state.py: _deferred_lock_state can also go
stale via a third path -- an active shake being interrupted by a state
that is neither "active" (early-return path) nor one of the lock-family
states the shake-interrupt handler explicitly defers for. The generic
per-state animation reset silences _anim_shake_active unconditionally
without touching _deferred_lock_state, orphaning it for a later,
unrelated shake to wrongly consume.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import island  # noqa: E402
from states import get_state  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def test_ordinary_state_interrupting_a_shake_clears_deferred_lock_state():
    widget = island.IslandWidget()

    # A shake is actively playing (e.g. from an earlier "stranger" state)...
    widget._state = get_state("stranger")
    widget._anim_shake_active = True

    # ...and a lock-family state arrives while it's playing: this defers
    # rather than interrupting outright (existing, correct behavior).
    widget.apply_state(get_state("social_lock"))
    assert widget._deferred_lock_state is not None
    assert widget._deferred_lock_state.name == "social_lock"
    assert widget._anim_shake_active is True, "deferring should not have touched the active shake"

    # Now an ordinary, everyday state neither "active" nor lock-family
    # interrupts the (still-playing) shake outright.
    widget.apply_state(get_state("scanning"))
    assert widget._anim_shake_active is False, "the interrupting state should have silenced the shake"

    assert widget._deferred_lock_state is None, (
        "a deferred lock-state target must not survive its own shake "
        "being interrupted by something unrelated - otherwise it sits "
        "orphaned until some later, unconnected shake wrongly consumes it"
    )
