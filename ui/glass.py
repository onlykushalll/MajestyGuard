"""Refractive "liquid glass" rendering for the lock overlay and the island.

A lens is a rounded rectangle whose edge band bends the backdrop behind it
like a thick convex piece of glass:

  * signed-distance field of the rounded rectangle gives the edge normal
    and the depth into the glass;
  * inside the bezel band, backdrop samples are pulled from further out
    along the normal (strongest at the rim, zero past the bezel), so the
    scene visibly bends around the edge while the centre stays flat;
  * red and blue sample the backdrop with slightly different strength
    (chromatic dispersion), giving the coloured fringe real glass has;
  * two rim lights (key from the top-left, a weaker fill from the
    bottom-right) plus a faint Fresnel ring finish the edge.

Cost model: the displacement/lighting maps depend only on the lens size, so
they are cached; a render is three cv2.remap calls on a small patch. Callers
cache the rendered image and only re-render when size, position or the
backdrop changes. Everything here is Qt-free except ``bgra_to_qimage``.

``MG_UI_GLASS=0`` disables the effect; callers keep their flat fallback
paint when ``glass_enabled()`` is false or no backdrop is available.
"""
from __future__ import annotations

import functools
import os

import numpy as np

try:  # cv2 is a hard runtime dependency of the daemon; guard for tooling.
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

_KEY_LIGHT = (-0.58, -0.81)   # unit-ish vector pointing at the light, top-left
_FILL_LIGHT = (0.71, 0.71)    # bottom-right fill


def glass_available() -> bool:
    return cv2 is not None


def glass_enabled() -> bool:
    return glass_available() and os.environ.get("MG_UI_GLASS", "1") != "0"


@functools.lru_cache(maxsize=64)
def lens_maps(w: int, h: int, radius: float, bezel: float, strength: float):
    """Size-only maps for a lens: (grid_x, grid_y, dx, dy, edge, spec, coverage).

    All arrays are float32 (h, w), read-only. dx/dy are the refraction
    displacement in logical pixels at unit strength scaling already applied.
    """
    w = max(2, int(w))
    h = max(2, int(h))
    radius = float(max(1.0, min(radius, min(w, h) / 2.0)))
    bezel = float(max(1.0, bezel))

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    px = xs + 0.5 - w / 2.0
    py = ys + 0.5 - h / 2.0
    qx = np.abs(px) - (w / 2.0 - radius)
    qy = np.abs(py) - (h / 2.0 - radius)
    ox = np.maximum(qx, 0.0)
    oy = np.maximum(qy, 0.0)
    outer = np.hypot(ox, oy)
    sdf = outer + np.minimum(np.maximum(qx, qy), 0.0) - radius  # <0 inside

    sx = np.where(px >= 0.0, 1.0, -1.0).astype(np.float32)
    sy = np.where(py >= 0.0, 1.0, -1.0).astype(np.float32)
    safe = np.maximum(outer, 1e-6)
    in_corner = outer > 1e-6
    horizontal = qx > qy
    nx = np.where(in_corner, sx * ox / safe, np.where(horizontal, sx, 0.0))
    ny = np.where(in_corner, sy * oy / safe, np.where(horizontal, 0.0, sy))

    depth = np.maximum(-sdf, 0.0)
    t = np.clip(depth / bezel, 0.0, 1.0)
    edge = (1.0 - t) ** 2.2                      # 1 at the rim, 0 past the bezel
    shift = strength * edge
    dx = (nx * shift).astype(np.float32)
    dy = (ny * shift).astype(np.float32)

    rim = (1.0 - t) ** 3.6
    key = np.maximum(nx * _KEY_LIGHT[0] * -1.0 + ny * _KEY_LIGHT[1] * -1.0, 0.0) ** 2.4
    fill = np.maximum(nx * _FILL_LIGHT[0] + ny * _FILL_LIGHT[1], 0.0) ** 2.4
    fresnel = (1.0 - t) ** 1.6
    spec = np.clip(rim * (0.95 * key + 0.30 * fill) + 0.10 * fresnel * (1.0 - t * 0.4), 0.0, 1.0)

    coverage = np.clip(0.5 - sdf, 0.0, 1.0)

    out = (xs.astype(np.float32), ys.astype(np.float32), dx, dy,
           edge.astype(np.float32), spec.astype(np.float32), coverage.astype(np.float32))
    for a in out:
        a.setflags(write=False)
    return out


def default_bezel(w: int, h: int) -> float:
    return float(max(5.0, min(26.0, min(w, h) * 0.30)))


def render_lens(
    src: np.ndarray,
    ox: float,
    oy: float,
    w: int,
    h: int,
    radius: float,
    *,
    scale: float = 1.0,
    bezel: float | None = None,
    strength: float | None = None,
    dispersion: float = 0.07,
    tint=(255, 255, 255),
    tint_alpha: float = 0.12,
    saturation: float = 1.18,
    spec_gain: float = 1.0,
) -> np.ndarray:
    """Render one lens to premultiplied BGRA uint8, shape (h, w, 4).

    ``src`` is the BGR backdrop (H, W, 3 uint8). ``(ox, oy)`` is the lens
    top-left in logical pixels, ``scale`` converts logical pixels to ``src``
    pixels (0.5 for a half-resolution backdrop).
    """
    if cv2 is None:
        raise RuntimeError("cv2 unavailable")
    w = max(2, int(w))
    h = max(2, int(h))
    bz = default_bezel(w, h) if bezel is None else float(bezel)
    st = bz * 0.78 if strength is None else float(strength)
    gx, gy, dx, dy, _edge, spec, cov = lens_maps(w, h, float(radius), bz, st)

    base_x = gx + float(ox)
    base_y = gy + float(oy)
    planes = [np.ascontiguousarray(src[:, :, i]) for i in range(3)]  # B, G, R
    channels = []
    for plane, k in zip(planes, (1.0 - dispersion, 1.0, 1.0 + dispersion)):
        mx = ((base_x + dx * k) * scale).astype(np.float32)
        my = ((base_y + dy * k) * scale).astype(np.float32)
        channels.append(
            cv2.remap(plane, mx, my, interpolation=cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REFLECT)
        )
    rgb = np.dstack(channels).astype(np.float32)  # B, G, R

    if saturation != 1.0:
        lum = rgb[:, :, 0] * 0.114 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.299
        rgb = lum[:, :, None] + (rgb - lum[:, :, None]) * float(saturation)

    ta = float(np.clip(tint_alpha, 0.0, 1.0))
    tint_bgr = np.array([tint[2], tint[1], tint[0]], dtype=np.float32)
    rgb = rgb * (1.0 - ta) + tint_bgr * ta

    light = (spec * float(spec_gain))[:, :, None]
    rgb = rgb * (1.0 - light) + 255.0 * light
    rgb = np.clip(rgb, 0.0, 255.0)

    out = np.empty((h, w, 4), dtype=np.uint8)
    out[:, :, 0:3] = (rgb * cov[:, :, None] + 0.5).astype(np.uint8)
    out[:, :, 3] = (cov * 255.0 + 0.5).astype(np.uint8)
    return out


# (cx, cy, radius, RGB, strength) in unit coordinates; soft pastel light field.
_AMBIENT_ORBS = (
    (0.20, 0.20, 0.34, (255, 255, 255), 0.70),
    (0.78, 0.24, 0.30, (140, 200, 255), 0.62),
    (0.72, 0.78, 0.32, (255, 170, 205), 0.58),
    (0.16, 0.82, 0.26, (190, 170, 255), 0.55),
    (0.50, 0.55, 0.22, (150, 232, 205), 0.42),
    (0.52, 0.06, 0.20, (255, 214, 160), 0.38),
)


@functools.lru_cache(maxsize=4)
def _ambient_cached(w: int, h: int) -> np.ndarray:
    sw, sh = max(8, w // 6), max(8, h // 6)
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    u = xs / max(1, sw - 1)
    v = ys / max(1, sh - 1)
    aspect = w / float(h)
    top = np.array((250.0, 251.0, 255.0), np.float32)
    bottom = np.array((228.0, 235.0, 247.0), np.float32)
    img = top[None, None, :] * (1.0 - v[:, :, None]) + bottom[None, None, :] * v[:, :, None]
    for cx, cy, radius, rgb, strength in _AMBIENT_ORBS:
        d2 = ((u - cx) * aspect) ** 2 + (v - cy) ** 2
        a = (strength * np.exp(-d2 / (radius * radius)))[:, :, None]
        img = img * (1.0 - a) + np.array(rgb, np.float32)[None, None, :] * a
    vig = 1.0 - 0.07 * (((u - 0.5) * 1.6) ** 2 + ((v - 0.5) * 1.6) ** 2)
    img = np.clip(img * vig[:, :, None], 0.0, 255.0).astype(np.uint8)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)
    out = np.ascontiguousarray(img[:, :, ::-1])            # RGB -> BGR
    out.setflags(write=False)
    return out


def build_ambient(w: int, h: int) -> np.ndarray:
    """Procedural pastel light field (BGR uint8, h x w, read-only).

    The lock backdrop never contains anything from the real screen: the
    overlay and the glass lenses refract this instead, so the desktop
    behind a lock leaks no colours, shapes or layout hints.
    """
    if cv2 is None:
        raise RuntimeError("cv2 unavailable")
    return _ambient_cached(max(16, int(w)), max(16, int(h)))


def bgra_to_qimage(arr: np.ndarray):
    """Premultiplied BGRA (h, w, 4) -> QImage that owns its memory."""
    from PyQt6.QtGui import QImage

    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def qimage_to_bgr(image) -> np.ndarray:
    """QImage -> contiguous BGR uint8 array (copy)."""
    from PyQt6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    raw = np.frombuffer(img.constBits().asstring(stride * h), dtype=np.uint8)
    return np.ascontiguousarray(raw.reshape(h, stride // 4, 4)[:, :w, 0:3])
