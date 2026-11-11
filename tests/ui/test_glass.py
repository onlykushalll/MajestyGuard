from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))

import glass  # noqa: E402


def _backdrop(h=240, w=320):
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 0] = (xx * 255 // w)
    img[:, :, 1] = (yy * 255 // h)
    img[:, :, 2] = 128
    return img


def test_shape_dtype_and_premultiplied():
    out = glass.render_lens(_backdrop(), 60, 60, 160, 80, 24)
    assert out.shape == (80, 160, 4) and out.dtype == np.uint8
    assert (out[:, :, :3] <= out[:, :, 3:4]).all()


def test_corners_transparent_centre_opaque():
    out = glass.render_lens(_backdrop(), 60, 60, 160, 80, 24)
    assert out[0, 0, 3] == 0 and out[-1, -1, 3] == 0
    assert out[40, 80, 3] == 255


def test_centre_matches_backdrop_when_untinted():
    src = _backdrop()
    out = glass.render_lens(src, 60, 60, 160, 80, 24, tint_alpha=0.0, saturation=1.0)
    cy, cx = 40, 80
    assert np.abs(out[cy, cx, :3].astype(int) - src[60 + cy, 60 + cx].astype(int)).max() <= 1


def test_rim_is_displaced():
    src = _backdrop()
    flat = glass.render_lens(src, 60, 60, 160, 80, 24, strength=0.001,
                             tint_alpha=0, saturation=1, spec_gain=0)
    bent = glass.render_lens(src, 60, 60, 160, 80, 24,
                             tint_alpha=0, saturation=1, spec_gain=0)
    assert np.abs(flat[40, 3, :3].astype(int) - bent[40, 3, :3].astype(int)).max() > 3


def test_maps_cached_and_readonly():
    a = glass.lens_maps(100, 50, 20.0, 15.0, 11.0)
    b = glass.lens_maps(100, 50, 20.0, 15.0, 11.0)
    assert a is b
    assert not a[2].flags.writeable


def test_disable_env(monkeypatch):
    monkeypatch.setenv("MG_UI_GLASS", "0")
    assert not glass.glass_enabled()
    monkeypatch.setenv("MG_UI_GLASS", "1")
    assert glass.glass_enabled()


def test_tiny_and_oversized_radius_do_not_crash():
    glass.render_lens(_backdrop(), 0, 0, 3, 3, 50)
    glass.render_lens(_backdrop(), 300, 220, 80, 60, 10)
