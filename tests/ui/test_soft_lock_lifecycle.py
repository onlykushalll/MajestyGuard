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
    assert calls[:4] == ["install", "taskbar:False", "uninstall", "taskbar:True"]
    calls_after_first_dissolve = list(calls)
    overlay.dissolve()
    assert calls == calls_after_first_dissolve
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


def test_delayed_backdrop_is_discarded_after_unlock_begins(qapp):
    """A background-blur worker from the prior lock must not repaint glass
    after ACTIVE has begun its dissolve animation."""
    from PyQt6.QtGui import QImage

    overlay = soft_lock.SoftLockOverlay()
    overlay._lock_generation = 7
    overlay._state = get_state("active")
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)

    overlay._on_background_ready(image, capture_generation=7)

    assert overlay._background.isNull()


def test_animate_opacity_fade_in_does_not_hide_overlay_on_early_finish(qapp):
    overlay = soft_lock.SoftLockOverlay()
    overlay.show()
    overlay._animate_opacity(0.0, 1.0)
    overlay._opacity_value = 0.01
    overlay._fade.finished.emit()

    assert overlay.isVisible() is True
    overlay.hide()


def test_fit_virtual_screen_tracks_virtual_geometry(monkeypatch, qapp):
    from PyQt6.QtCore import QRect

    overlay = soft_lock.SoftLockOverlay()

    class MockScreen:
        def virtualGeometry(self):
            return QRect(0, 0, 3840, 1080)

    monkeypatch.setattr(soft_lock.QGuiApplication, "primaryScreen", lambda: MockScreen())
    overlay._fit_virtual_screen()

    assert overlay.geometry() == QRect(0, 0, 3840, 1080)

