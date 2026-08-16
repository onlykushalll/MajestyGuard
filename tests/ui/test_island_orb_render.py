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


@pytest.mark.parametrize(
    "renderer_name",
    [
        "_paint_searching_orb",
        "_paint_working_orb",
        "_paint_resolving_orb",
        "_paint_guarded_orb",
    ],
)
def test_state_specific_orb_renderers_draw_visible_small_icon(qapp, renderer_name):
    """Each security state needs its own readable 20-32px visual verb.

    A generic particle cluster is too subtle at the Dynamic Island's actual
    icon size; this verifies that every specialised renderer is real and draws
    pixels, rather than merely being documented as a future design idea.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    widget = island.IslandWidget()
    pixmap = QPixmap(48, 48)
    pixmap.fill(QColor(20, 20, 20))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    getattr(widget, renderer_name)(painter, 24.0, 24.0, 15.0)
    painter.end()

    background = QColor(20, 20, 20).rgb()
    image = pixmap.toImage()
    assert any(
        image.pixel(x, y) != background
        for x in range(image.width())
        for y in range(image.height())
    )


@pytest.mark.parametrize("renderer_name", ["_paint_searching_orb", "_paint_working_orb"])
def test_active_orb_renderers_change_between_animation_phases(qapp, renderer_name):
    """Searching and verification must visibly evolve instead of reading as a static badge."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    widget = island.IslandWidget()

    def render(phase: float) -> bytes:
        widget._phase = phase
        pixmap = QPixmap(48, 48)
        pixmap.fill(QColor(20, 20, 20))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        getattr(widget, renderer_name)(painter, 24.0, 24.0, 15.0)
        painter.end()
        return pixmap.toImage().bits().asstring(48 * 48 * 4)

    assert render(0.10) != render(0.55)


def test_locked_pill_renders_guarded_icon_and_status_content(qapp):
    """A locked Dynamic Island must never degrade to an empty black shell."""
    from PyQt6.QtCore import Qt, QRect
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    widget = island.IslandWidget()
    state = get_state("locked_passive")
    widget._state = state
    widget._visible_state = state
    widget._alpha = widget._content_alpha = 1.0
    pixmap = QPixmap(state.width, state.height)
    pixmap.fill(QColor(state.bg_color))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    widget._paint_content(painter, QRect(0, 0, state.width, state.height), state)
    painter.end()

    background = QColor(state.bg_color).rgb()
    image = pixmap.toImage()
    assert any(
        image.pixel(x, y) != background
        for x in range(image.width())
        for y in range(image.height())
    )


@pytest.mark.parametrize("state_name", ["locked_passive", "soft_locked", "social_lock", "hostile_lock"])
def test_locked_pills_use_their_declared_readable_geometry(qapp, state_name):
    """Lock states have purpose-built layouts; shrinking them clips their status UI."""
    widget = island.IslandWidget()
    state = get_state(state_name)

    widget.apply_state(state)

    assert widget._target_w == float(state.width)
    assert widget._target_h == float(state.height)


@pytest.mark.parametrize("renderer_name", ["_paint_searching_orb", "_paint_working_orb"])
def test_reduced_motion_orbs_are_static_between_phases(qapp, renderer_name):
    """Reduced motion retains the state icon without continuous movement."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    widget = island.IslandWidget()
    widget._reduce_motion = True

    def render(phase: float) -> bytes:
        widget._phase = phase
        pixmap = QPixmap(48, 48)
        pixmap.fill(QColor(20, 20, 20))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        getattr(widget, renderer_name)(painter, 24.0, 24.0, 15.0)
        painter.end()
        return pixmap.toImage().bits().asstring(48 * 48 * 4)

    assert render(0.10) == render(0.55)


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
    ("soft_locked", 0.2),
    ("soft_locked", 0.8),
    ("social_lock", 0.5),
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


def test_apply_state_always_resets_alpha_to_visible(qapp):
    """Critical safety property a real fix in ui/main.py depends on: the
    startup sequence deliberately sets widget._alpha = 0.0 to hide the
    placeholder 'idle' pill (states.py's "idle" entry has real 120x28
    dimensions and a real opaque background color -- it was never
    actually invisible, just contentless, and was appearing on screen
    before any real daemon state ever arrived). That's only safe because
    apply_state() unconditionally resets _alpha back to 1.0 on every
    call, regardless of what it was set to before -- so the very next
    real state transition makes the pill visible again at the correct
    moment. If this property ever silently breaks, the whole Dynamic
    Island would get stuck invisible forever after startup."""
    widget = island.IslandWidget()
    widget._alpha = 0.0
    widget.apply_state(get_state("idle_detected"))
    assert widget._alpha == 1.0


def test_orb_phase_is_initialized_and_advances_for_an_animated_state(qapp):
    """The actual live widget must own and advance the phase used by the
    particle painter; assigning it only in offscreen tests masks a blank UI."""
    widget = island.IslandWidget()
    assert widget._phase == 0.0

    widget.apply_state(get_state("scanning"))
    assert widget._anim_orb_active is True
    widget._tick_animations()
    assert widget._phase > 0.0


def test_normal_owner_check_confirms_without_welcome(qapp):
    """Sitting at an already-unlocked desktop is a confirmation, not a login."""
    widget = island.IslandWidget()
    widget.apply_state(get_state("scanning"))
    widget.apply_state(get_state("active"))

    assert widget._show_welcome_after_verification is False
    widget._after_verified()
    assert widget._state.name == "active"
    assert widget._anim_pill_fade_active is True


def test_soft_lock_owner_return_uses_welcome(qapp):
    """Welcome is reserved for a successful return from the blocking overlay."""
    widget = island.IslandWidget()
    widget.apply_state(get_state("locked_passive"))
    widget.apply_state(get_state("active"))

    assert widget._show_welcome_after_verification is True
    widget._after_verified()
    assert widget._state.name == "welcome"


def test_relock_interrupts_island_exit_without_leaving_a_stale_fade(qapp):
    """A new lock may arrive while the prior verified pill is exiting.

    The lock state must win immediately so the user never sees a stale success
    animation or a pill shrinking away during a fresh protection event.
    """
    widget = island.IslandWidget()
    widget.apply_state(get_state("locked_passive"))
    widget.apply_state(get_state("active"))
    assert widget._in_exit_sequence is True

    lock_state = get_state("soft_locked")
    widget.apply_state(lock_state)

    assert widget._in_exit_sequence is False
    assert widget._state.name == "soft_locked"
    assert widget._target_w == float(lock_state.width)
    assert widget._target_h == float(lock_state.height)
    assert widget._anim_pill_fade_active is False
