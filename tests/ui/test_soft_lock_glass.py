"""Overlay liquid-glass wiring: backdrop hand-off, stale-source guards,
paint fallbacks and the partial-repaint region."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
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
def overlay(qapp, monkeypatch):
    monkeypatch.setattr(soft_lock, "_install_hooks", lambda _o: None)
    monkeypatch.setattr(soft_lock, "_uninstall_hooks", lambda: None)
    monkeypatch.setattr(soft_lock, "_set_taskbar_visible", lambda _v: None)
    monkeypatch.setattr(soft_lock, "_stop_background_media", lambda: None)
    # No real screen grab / worker thread: tests inject the backdrop themselves.
    monkeypatch.setattr(soft_lock.SoftLockOverlay, "_capture_background", lambda self: None)
    o = soft_lock.SoftLockOverlay()
    o.apply_state(get_state("soft_locked"))
    yield o
    o.hide()


def _src(w=400, h=300):
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 0] = xx * 255 // w
    img[:, :, 1] = yy * 255 // h
    img[:, :, 2] = 140
    return img


def test_lens_source_accepted_for_current_lock(overlay):
    src = _src()
    overlay._on_lens_source_ready(src, overlay._lock_generation)
    assert overlay._lens_src is src


def test_lens_source_dropped_for_stale_generation(overlay):
    overlay._on_lens_source_ready(_src(), overlay._lock_generation - 1)
    assert overlay._lens_src is None


def test_lens_source_dropped_outside_lock_state(overlay):
    overlay._state = get_state("active", 0.9, 0.9)
    overlay._on_lens_source_ready(_src(), overlay._lock_generation)
    assert overlay._lens_src is None


def test_capture_clears_previous_lens_source(qapp, monkeypatch):
    monkeypatch.setattr(soft_lock, "_install_hooks", lambda _o: None)
    monkeypatch.setattr(soft_lock, "_uninstall_hooks", lambda: None)
    monkeypatch.setattr(soft_lock, "_set_taskbar_visible", lambda _v: None)
    monkeypatch.setattr(soft_lock, "_stop_background_media", lambda: None)
    o = soft_lock.SoftLockOverlay()
    o._lens_src = _src()
    o._lens_cache[("k",)] = object()
    monkeypatch.setattr(o, "_set_capture_protection", lambda _v: None)
    o._capture_background()
    assert o._lens_src is None and not o._lens_cache


def test_lens_image_cached_and_disable_switch(overlay, monkeypatch):
    from PyQt6.QtCore import QRectF

    overlay._lens_src = _src()
    rect = QRectF(40.0, 30.0, 200.0, 60.0)
    a = overlay._lens_image(rect, 30.0, tint_alpha=0.2)
    b = overlay._lens_image(rect, 30.0, tint_alpha=0.2)
    assert a is not None and a is b
    assert (a.width(), a.height()) == (200, 60)
    monkeypatch.setenv("MG_UI_GLASS", "0")
    assert overlay._lens_image(rect, 30.0, tint_alpha=0.2) is None


def test_paint_works_with_and_without_glass(overlay):
    from PyQt6.QtGui import QPixmap

    overlay.resize(800, 600)
    overlay._opacity_value = 1.0
    flat = overlay.grab()
    assert not flat.isNull()
    overlay._lens_src = _src(400, 300)
    glass = overlay.grab()
    assert not glass.isNull()
    assert glass.toImage() != flat.toImage(), "glass paint left the frame unchanged"


def test_dynamic_region_covers_animated_hud(overlay):
    from PyQt6.QtCore import QPoint

    overlay.resize(800, 600)
    lay = overlay._layout()
    region = overlay._dynamic_region()

    def centre(rect):
        x, y, w, h = rect
        return QPoint(int(x + w / 2), int(y + h / 2))

    # clock card, both key caps, the status chip and the brand line all animate
    for rect in (lay.card, lay.space_key, lay.tab_key, lay.status, lay.brand):
        assert region.contains(centre(rect)), rect


def test_tick_updates_partially_then_fully(overlay, monkeypatch):
    overlay.show()
    overlay._opacity_value = 1.0
    calls = []
    monkeypatch.setattr(overlay, "update", lambda *a: calls.append(a))
    overlay._tick_count = 0
    for _ in range(30):
        overlay._tick()
    partial = [c for c in calls if c]
    full = [c for c in calls if not c]
    assert len(full) == 1 and len(partial) == 29


@pytest.mark.parametrize("name", ["verifying_lock", "social_lock", "hostile_lock", "verify_failed"])
def test_clock_paints_in_every_lock_state(overlay, name):
    """The state aura used QPointF, which soft_lock never imported: a
    NameError inside paintEvent (hard abort under PyQt6 without an
    excepthook) in every state except locked/soft_locked."""
    from PyQt6.QtGui import QImage, QPainter

    overlay.resize(800, 600)
    overlay._state = get_state(name)
    img = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        overlay._paint_clock(painter)
    finally:
        painter.end()


@pytest.mark.parametrize("glass_enabled", [False, True], ids=["fallback", "refraction"])
def test_windows_lock_action_stays_light_when_painted_after_space(overlay, monkeypatch, glass_enabled):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QImage, QPainter

    monkeypatch.setenv("MG_UI_GLASS", "1" if glass_enabled else "0")
    overlay._lens_src = np.full((45, 500, 3), (48, 42, 38), dtype=np.uint8)
    image = QImage(1000, 68, QImage.Format.Format_ARGB32)
    image.fill(QColor(233, 237, 243))
    space, tab = soft_lock._layout.key_models("camera_fault")
    if glass_enabled:
        assert overlay._lens_image(QRectF(500, 0, 500, 68), 34) is not None
    painter = QPainter(image)
    try:
        overlay._paint_key(painter, QRectF(0, 0, 500, 68), space, 1.0)
        overlay._paint_key(painter, QRectF(500, 0, 500, 68), tab, 1.0)
    finally:
        painter.end()

    space_surface = image.pixelColor(450, 34)
    tab_surface = image.pixelColor(950, 34)
    assert min(tab_surface.red(), tab_surface.green(), tab_surface.blue()) >= 150
    assert tab_surface == space_surface
