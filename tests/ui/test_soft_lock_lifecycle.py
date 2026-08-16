"""Lifecycle tests for rapid soft-lock re-engagement."""
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


def test_relock_cancels_stale_dissolve_and_reinstalls_hooks(monkeypatch, qapp):
    """A lock arriving during the old 600ms unlock animation must retain
    visibility and input ownership; stale completion cannot tear it down."""
    calls: list[str] = []
    monkeypatch.setattr(soft_lock, "_install_hooks", lambda _overlay: calls.append("install"))
    monkeypatch.setattr(soft_lock, "_uninstall_hooks", lambda: calls.append("uninstall"))
    monkeypatch.setattr(soft_lock, "_set_taskbar_visible", lambda visible: calls.append(f"taskbar:{visible}"))
    monkeypatch.setattr(soft_lock, "_stop_background_media", lambda: None)

    overlay = soft_lock.SoftLockOverlay()
    overlay.apply_state(get_state("soft_locked"))
    overlay.apply_state(get_state("active"))
    old_generation = overlay._dissolve_generation
    overlay.apply_state(get_state("soft_locked"))
    qapp.processEvents()

    assert overlay._lock_generation != old_generation
    assert calls.count("install") == 2
    assert calls[-1] == "taskbar:False"

    overlay._hide_after_dissolve()

    assert overlay.isVisible()
    assert calls.count("uninstall") == 1
    overlay.hide()
