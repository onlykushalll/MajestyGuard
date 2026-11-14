"""The lock backdrop must never contain anything from the real screen."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import soft_lock  # noqa: E402
from states import get_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_capture_never_grabs_the_screen(qapp, monkeypatch):
    from PyQt6.QtGui import QScreen

    def boom(*_a, **_k):
        raise AssertionError("the lock backdrop must not read the real screen")

    monkeypatch.setattr(soft_lock, "_install_hooks", lambda _o: None)
    monkeypatch.setattr(soft_lock, "_uninstall_hooks", lambda: None)
    monkeypatch.setattr(soft_lock, "_set_taskbar_visible", lambda _v: None)
    monkeypatch.setattr(soft_lock, "_stop_background_media", lambda: None)
    monkeypatch.setattr(QScreen, "grabWindow", boom, raising=False)
    overlay = soft_lock.SoftLockOverlay()
    overlay.apply_state(get_state("soft_locked"))   # runs _capture_background
    time.sleep(0.2)                                 # let the worker finish
    overlay.hide()


def test_ambient_image_is_generated_and_sized(qapp):
    img = soft_lock.SoftLockOverlay._ambient_image(320, 200)
    assert not img.isNull()
    assert (img.width(), img.height()) == (320, 200)
    # generated light field: bright, not a flat fill
    px = {img.pixelColor(x, y).rgb() for x in (10, 160, 300) for y in (10, 100, 190)}
    assert len(px) > 3


def test_ambient_image_falls_back_to_solid_without_glass(qapp, monkeypatch):
    monkeypatch.setattr(soft_lock, "_glass", None)
    img = soft_lock.SoftLockOverlay._ambient_image(64, 48)
    assert (img.width(), img.height()) == (64, 48)
    assert img.pixelColor(5, 5).rgb() == img.pixelColor(50, 40).rgb()
