"""The daemon broadcasts "active" on every ACTIVE frame (confidence and
liveness vary, so the pipe reader's identical-payload dedup never drops
them). Those repeats arrive while the overlay's 600ms unlock dissolve is
still running. A repeat of a non-lock state must leave the dissolve
alone; only a lock-family repeat may re-assert lock ownership.
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

import soft_lock  # noqa: E402
from states import get_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def hook_calls(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(soft_lock, "_install_hooks", lambda _overlay: calls.append("install"))
    monkeypatch.setattr(soft_lock, "_uninstall_hooks", lambda: calls.append("uninstall"))
    monkeypatch.setattr(soft_lock, "_set_taskbar_visible", lambda visible: calls.append(f"taskbar:{visible}"))
    monkeypatch.setattr(soft_lock, "_stop_background_media", lambda: None)
    return calls


def test_repeated_active_during_dissolve_does_not_relock(qapp, hook_calls):
    from PyQt6.QtCore import QAbstractAnimation

    overlay = soft_lock.SoftLockOverlay()
    overlay.apply_state(get_state("soft_locked"))
    overlay.apply_state(get_state("active", 0.81, 0.90))
    assert overlay._dissolve_anim.state() == QAbstractAnimation.State.Running

    generation = overlay._lock_generation
    installs = hook_calls.count("install")

    # The next ACTIVE frame: same state name, different confidence.
    overlay.apply_state(get_state("active", 0.83, 0.91))

    assert overlay._lock_generation == generation, "a repeated active must not bump the lock generation"
    assert hook_calls.count("install") == installs, "input hooks were reinstalled during an unlock"
    assert hook_calls[-1] == "taskbar:True", "the taskbar was hidden again during an unlock"
    assert overlay._dissolve_anim.state() == QAbstractAnimation.State.Running, "the dissolve was cancelled"
    overlay.hide()


def test_repeated_lock_state_still_reasserts_lock_ownership(qapp, hook_calls):
    overlay = soft_lock.SoftLockOverlay()
    overlay.apply_state(get_state("soft_locked"))
    assert hook_calls.count("install") == 1

    overlay.apply_state(get_state("soft_locked"))

    assert hook_calls.count("install") == 2
    assert hook_calls[-1] == "taskbar:False"
    overlay.hide()
