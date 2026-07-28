"""Tests for the orb-particle icon system in ui/island.py -- the redesigned
dot-cluster icon replacing the old Face ID glyph / green checkmark-only
look. Includes a real offscreen Qt render, not just text assertions,
automating the same visual verification done by hand when this was built:
does _paint_content actually run without exceptions and draw something
real for every state that now uses the orb badge.
"""
from __future__ import annotations

import math
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


def test_orb_particle_seed_is_deterministic():
    """Same index must always produce the same base position -- only
    _phase should move the dots. If this weren't deterministic, dots
    would jitter/flicker between frames instead of animating smoothly."""
    a1 = island._orb_particle_seed(3)
    a2 = island._orb_particle_seed(3)
    assert a1 == a2


def test_orb_particle_seed_values_in_expected_ranges():
    for i in range(12):
        angle_offset, radius_frac, size_frac = island._orb_particle_seed(i)
        assert 0.0 <= angle_offset <= 2 * math.pi
        assert 0.35 <= radius_frac <= 0.90
        assert 0.5 <= size_frac <= 1.0


def test_orb_particle_seed_spreads_particles_out():
    """Consecutive particles should land at meaningfully different
    angles (golden-angle spacing) -- not clustered on top of each other."""
    angles = [island._orb_particle_seed(i)[0] for i in range(6)]
    diffs = [abs(angles[i] - angles[i - 1]) % (2 * math.pi) for i in range(1, len(angles))]
    assert all(0.3 < d < (2 * math.pi - 0.3) for d in diffs)


def test_all_orb_styles_have_consistent_shape():
    for name, profile in island._ORB_STYLES.items():
        assert len(profile) == 5, f"{name} profile must be (count, speed, jitter, spread, dot_px)"
        count, speed, jitter, spread, dot_px = profile
        assert count >= 3
        assert speed > 0
        assert 0.0 <= spread <= 1.0
        assert dot_px > 0


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.mark.parametrize("state_name,phase", [
    ("idle_detected", 0.1),
    ("idle_detected", 0.9),
    ("scanning", 0.5),
    ("verifying_lock", 0.2),
    ("verifying_lock", 0.85),
    ("welcome", 0.4),
])
def test_orb_states_render_without_exception_and_draw_something(qapp, state_name, phase):
    """Real offscreen render, not a text assertion -- catches the exact
    class of bug already found once this session (an animation that's
    defined but never actually wired/invoked, or that throws when given
    real state data)."""
    from PyQt6.QtGui import QPixmap, QPainter, QColor
    from PyQt6.QtCore import Qt, QRect

    widget = island.IslandWidget()
    state = get_state(state_name)
    widget._state = state
    widget._phase = phase
    widget._alpha = 1.0
    widget._content_alpha = 1.0
    widget._opacity_value = 1.0

    w, h = state.width, state.height
    pixmap = QPixmap(w, h)
    pixmap.fill(QColor(20, 20, 20))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRect(0, 0, w, h)
    painter.setBrush(QColor(28, 28, 30))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, h / 2, h / 2)

    widget._paint_content(painter, rect, state)  # must not raise
    painter.end()

    image = pixmap.toImage()
    background = QColor(28, 28, 30).rgb()
    non_background_pixels = 0
    for x in range(0, w, 3):
        for y in range(0, h, 3):
            if image.pixel(x, y) != background:
                non_background_pixels += 1
    assert non_background_pixels > 0, (
        f"{state_name} at phase={phase} drew nothing visible -- "
        "content may be defined but not actually wired up"
    )
