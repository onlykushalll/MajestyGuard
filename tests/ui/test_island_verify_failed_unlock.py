"""A successful owner check after a failed one must unlock like any other lock
state: the island plays its exit/welcome sequence instead of just vanishing
while the overlay dissolves."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import island  # noqa: E402
from states import get_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


@pytest.mark.parametrize("lock", ["locked_passive", "soft_locked", "verifying_lock", "social_lock",
                                  "hostile_lock", "verify_failed"])
def test_unlock_from_any_lock_state_starts_exit_sequence_with_welcome(qapp, lock):
    w = island.IslandWidget()
    w.show()
    w.apply_state(get_state(lock))
    w.apply_state(get_state("active", 0.9, 0.9))
    assert w._in_exit_sequence, f"unlock from {lock} did not start the exit sequence"
    w.hide()
