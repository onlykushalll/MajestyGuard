"""Regression test for a real bug found while debugging ui/island.py.

verifying_lock -> social_lock defers via the shared min-duration
_defer_timer (redirecting to a "stranger" shake first, once it fires).
If verifying_lock -> active arrives before that timer fires, "active" is
handled via an early return (_begin_exit_sequence) that never touches
_defer_timer at all. The old timer is left armed. When it eventually
fires for real, it calls apply_state(stranger) -- and since the welcome
sequence is now in progress, the very first check in apply_state aborts
it to show the stale stranger shake instead. See the comment on
_begin_exit_sequence in island.py for the full mechanism and fix.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import island  # noqa: E402
from states import get_state  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def test_stale_defer_timer_does_not_abort_a_later_active_welcome():
    widget = island.IslandWidget()
    widget._state = get_state("verifying_lock")
    widget._verify_start_time = time.monotonic()  # recent: min-duration not yet elapsed

    widget.apply_state(get_state("social_lock"))
    assert widget._state.name == "verifying_lock", "should have deferred, not applied yet"
    assert widget._deferred_lock_state is not None
    assert hasattr(widget, "_defer_timer") and widget._defer_timer.isActive(), (
        "the min-duration timer should be armed after a deferred redirect"
    )

    # Backend decides the person is actually fine, before the deferred
    # stranger-shake redirect ever fired. "active" takes an early-return
    # path that never touches _defer_timer.
    widget.apply_state(get_state("active"))
    assert widget._in_exit_sequence is True, "active should have started the welcome/exit sequence"

    # The old timer is still armed and fires for real eventually. Emit it
    # directly rather than waiting on a real event loop -- deterministic.
    widget._defer_timer.timeout.emit()

    assert widget._in_exit_sequence is True, (
        "a stale deferred stranger-shake call must not abort an "
        "already-started, unrelated welcome/exit sequence"
    )
