"""Pure layout and copy for the lock overlay (no Qt)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

import lock_layout as L  # noqa: E402
import tokens  # noqa: E402


def _inside(inner, outer):
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ix >= ox - 0.5 and iy >= oy - 0.5 and ix + iw <= ox + ow + 0.5 and iy + ih <= oy + oh + 0.5


def test_layout_stays_on_screen_and_in_order():
    for w, h in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160), (800, 600)):
        lay = L.compute(0, 0, w, h)
        screen = (0, 0, w, h)
        for name in ("card", "space_key", "tab_key", "status", "brand"):
            assert _inside(getattr(lay, name), screen), (w, h, name)
        # status chip above the card (and clear of the island zone: top-centre), keys directly
        # under it, brand at the very bottom
        assert lay.status[1] + lay.status[3] < lay.card[1]
        assert lay.status[0] + lay.status[2] < w / 2.0 - 150.0 * lay.scale, (w, h)
        assert lay.space_key[1] >= lay.card[1] + lay.card[3]
        assert lay.brand[1] > lay.tab_key[1] + lay.tab_key[3]
        # keys sit side by side and together are exactly as wide as the card
        assert lay.space_key[0] + lay.space_key[2] < lay.tab_key[0]
        assert abs((lay.tab_key[0] + lay.tab_key[2]) - (lay.card[0] + lay.card[2])) < 0.5


def test_layout_respects_a_secondary_monitor_offset():
    lay = L.compute(1920, 0, 1920, 1080)
    assert 1920 <= lay.card[0] and lay.card[0] + lay.card[2] <= 3840
    assert abs(lay.cx - 2880) < 0.5


def test_animated_bounds_cover_every_moving_part():
    lay = L.compute(0, 0, 1600, 900)
    bounds = L.animated_bounds(lay)
    for name in ("card", "space_key", "tab_key", "status", "brand"):
        assert any(_inside(getattr(lay, name), b) for b in bounds), name


def test_every_lock_state_has_copy_and_two_keys():
    for name in ("locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock",
                 "verify_failed", "camera_fault"):
        headline, sub = L.message_for(name, "Camera is blocked. Details.")
        assert headline and sub
        space, tab = L.key_models(name)
        assert (space.key, tab.key) == ("SPACE", "TAB")
        assert space.label and tab.label


def test_camera_fault_needs_two_tab_presses_and_says_so():
    _, tab = L.key_models("camera_fault", tab_armed=False)
    assert "twice" in tab.sub.lower() and tab.mode == "idle"
    _, tab = L.key_models("camera_fault", tab_armed=True)
    assert tab.mode == "armed"
    assert L.message_for("camera_fault", "x", tab_armed=True)[0] == "Press TAB again"
    # a working camera hands over to Windows on the first press
    assert "twice" not in L.key_models("soft_locked")[1].sub.lower()


def test_face_check_is_not_offered_when_it_is_not_available():
    assert L.key_models("hostile_lock")[0].mode == "dim"
    assert L.key_models("verify_failed")[0].mode == "dim"
    assert L.key_models("verifying_lock")[0].mode == "busy"
    assert L.key_models("soft_locked")[0].mode == "idle"


def test_cooldown_detail_is_shown_verbatim():
    assert L.message_for("verify_failed", "Retry cooldown (7s)")[1] == "Retry cooldown (7s)"


def test_accents_cover_all_lock_states():
    for name in ("locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock",
                 "verify_failed", "camera_fault"):
        assert name in tokens.ACCENT_BY_STATE
    assert tokens.accent_for("something_else") == tokens.BLUE


def test_copy_fits_its_boxes_with_real_font_metrics():
    """Every status label / headline / sub line must fit un-elided, at every screen size."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QFont, QFontMetricsF
    from PyQt6.QtWidgets import QApplication

    import fonts

    app = QApplication.instance() or QApplication([])
    assert app is not None
    states = ("locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock",
              "verify_failed", "camera_fault")
    for w, h in ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440), (3840, 2160)):
        lay = L.compute(0, 0, w, h)
        s = lay.scale
        status_font = QFontMetricsF(fonts.ui_font(8.5 * s, QFont.Weight.DemiBold, spacing=1.1))
        head_font = QFontMetricsF(fonts.ui_font(16.5 * s, QFont.Weight.DemiBold))
        sub_font = QFontMetricsF(fonts.ui_font(10.5 * s, QFont.Weight.Normal))
        chip_room = lay.status[2] - (7.0 + 10.0) * s - 24.0 * s
        for name in states:
            label = L.status_label(name)
            # letter-spacing is applied by the paint code; metrics here are a lower bound, so allow 15%
            assert status_font.horizontalAdvance(label) * 1.15 <= chip_room + 40.0 * s, (w, h, name, label)
            for armed in (False, True):
                headline, sub = L.message_for(name, "Camera is blocked. Details.", armed)
                room = lay.message[2]
                assert head_font.horizontalAdvance(headline) <= room, (w, h, name, headline)
                assert sub_font.horizontalAdvance(sub) <= room, (w, h, name, sub)
