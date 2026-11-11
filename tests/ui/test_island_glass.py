"""Island liquid-glass body: backdrop comes from the parent overlay and the
flat body stays the fallback whenever no lock backdrop is available."""
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

import island  # noqa: E402
from states import get_state  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def _src(w=400, h=300):
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 0] = xx * 255 // w
    img[:, :, 1] = yy * 255 // h
    img[:, :, 2] = 140
    return img


def _make_island(qapp, provider):
    from PyQt6.QtWidgets import QWidget

    class _Parent(QWidget):
        pass

    parent = _Parent()
    if provider is not None:
        parent.lens_backdrop_for = provider
    w = island.IslandWidget()
    w.setParent(parent, w.windowFlags())
    return parent, w


def _rect_radius(w):
    from PyQt6.QtCore import QRect

    return QRect(10, 8, 220, 40), 20.0


def test_no_provider_means_flat_body(qapp):
    parent, w = _make_island(qapp, None)
    rect, r = _rect_radius(w)
    assert w._lens_body(rect, r, get_state("locked_passive")) is None


def test_provider_without_backdrop_means_flat_body(qapp):
    parent, w = _make_island(qapp, lambda _pos: None)
    rect, r = _rect_radius(w)
    assert w._lens_body(rect, r, get_state("locked_passive")) is None


def test_lens_body_rendered_cached_and_invalidated_by_new_backdrop(qapp):
    src1, src2 = _src(), _src()
    current = {"src": src1}
    parent, w = _make_island(qapp, lambda _pos: (current["src"], 90, 10))
    rect, r = _rect_radius(w)
    state = get_state("locked_passive")
    a = w._lens_body(rect, r, state)
    assert a is not None and (a.width(), a.height()) == (220, 40)
    assert w._lens_body(rect, r, state) is a, "same backdrop + geometry must hit the cache"
    current["src"] = src2
    b = w._lens_body(rect, r, state)
    assert b is not None and b is not a, "a new lock backdrop must not reuse old lenses"


def test_disable_switch(qapp, monkeypatch):
    parent, w = _make_island(qapp, lambda _pos: (_src(), 90, 10))
    rect, r = _rect_radius(w)
    monkeypatch.setenv("MG_UI_GLASS", "0")
    assert w._lens_body(rect, r, get_state("locked_passive")) is None


def test_provider_error_falls_back_to_flat(qapp):
    def boom(_pos):
        raise RuntimeError("overlay gone")

    parent, w = _make_island(qapp, boom)
    rect, r = _rect_radius(w)
    assert w._lens_body(rect, r, get_state("locked_passive")) is None


def test_paint_differs_with_glass(qapp):
    parent_flat, flat = _make_island(qapp, None)
    parent_glass, glass = _make_island(qapp, lambda _pos: (_src(), 90, 10))
    for w in (flat, glass):
        w.resize(500, 120)
        w.apply_state(get_state("locked_passive"))
    a = flat.grab().toImage()
    b = glass.grab().toImage()
    assert not a.isNull() and not b.isNull()
    assert a != b
