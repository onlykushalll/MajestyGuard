"""Pure layout and copy for the lock overlay (no Qt).

The overlay is keyboard-only: the mouse is pinned and its clicks are blocked
while locked, so there is nothing to click. Everything a person can do is shown
as a key cap under the clock -- SPACE to verify, TAB to fall back to the Windows
password -- and this module decides where those sit and what they say.
"""
from __future__ import annotations

from dataclasses import dataclass

Rect = tuple[float, float, float, float]  # x, y, w, h

_VERIFY_ENABLED = {"locked_passive", "soft_locked", "social_lock", "camera_fault"}


@dataclass(frozen=True)
class Layout:
    scale: float
    card: Rect          # the wide "ruler" glass card
    time: Rect          # clock digits, left of the card
    divider: Rect       # hairline between clock and text (x, y, 0, h)
    date: Rect
    message: Rect       # headline + supporting line, right of the divider
    ruler: Rect         # tick strip along the card's lower edge
    space_key: Rect
    tab_key: Rect
    status: Rect        # top-left chip
    brand: Rect         # bottom-right pill
    cx: float


def compute(x: float, y: float, w: float, h: float) -> Layout:
    """Layout for the primary-screen rectangle ``(x, y, w, h)``."""
    s = max(0.85, min(1.6, h / 900.0))
    margin = max(24.0, min(42.0, w / 48.0))
    cx = x + w / 2.0

    card_w = min(800.0 * s, w - 2.0 * margin)
    card_h = 176.0 * s
    key_h, key_gap, dock_gap = 68.0 * s, 16.0 * s, 18.0 * s
    group_h = card_h + dock_gap + key_h
    top = y + max(margin * 2.0 + 36.0 * s, (h - group_h) * 0.40)
    card = (cx - card_w / 2.0, top, card_w, card_h)

    key_w = (card_w - key_gap) / 2.0
    key_y = top + card_h + dock_gap
    space_key = (card[0], key_y, key_w, key_h)
    tab_key = (card[0] + key_w + key_gap, key_y, key_w, key_h)

    pad = 40.0 * s
    time_w = 292.0 * s
    time_rect = (card[0] + pad, top + 14.0 * s, time_w, card_h - 14.0 * s - 46.0 * s)
    div_x = card[0] + pad + time_w + 18.0 * s
    divider = (div_x, top + 26.0 * s, 0.0, card_h - 26.0 * s - 58.0 * s)
    text_x = div_x + 28.0 * s
    text_w = card[0] + card_w - pad - text_x
    date = (text_x, top + 24.0 * s, text_w, 24.0 * s)
    message = (text_x, top + 52.0 * s, text_w, 66.0 * s)
    ruler = (card[0] + pad, top + card_h - 36.0 * s, card_w - 2.0 * pad, 20.0 * s)

    status = (x + margin, y + margin, 200.0 * s, 36.0 * s)      # top-left, clear of the island
    if card_w < 630 * s:
        # Portrait / high scaling: stop competing for a shrinking message column.
        # Two full-width keyboard actions follow one stacked clock face.
        s = min(s, max(.85, w / 540))
        pad = 24 * s
        ch, kh, gap = 201*s, 53*s, 8*s
        status = cx-78*s, y+112, 156*s, 31*s
        top = max(status[1]+status[3]+12*s, y+(h-ch-2*kh-2*gap)*.42)
        left = cx - card_w/2
        room = card_w-2*pad
        card = left, top, card_w, ch
        time_rect = left+pad, top+4*s, room, 64*s
        divider = left+pad, top+70*s, room, 0.
        date = left+pad, top+75*s, room, 18*s
        message = left+pad, top+96*s, room, 66*s
        ruler = left+pad, top+ch-29*s, room, 14*s
        space_key = left, top+ch+gap, card_w, kh
        tab_key = left, top+ch+2*gap+kh, card_w, kh
    brand_w, brand_h = 236.0 * s, 34.0 * s
    brand = (x + w - margin - brand_w, y + h - margin - brand_h, brand_w, brand_h)
    return Layout(
        scale=s,
        card=card,
        time=time_rect,
        divider=divider,
        date=date,
        message=message,
        ruler=ruler,
        space_key=space_key,
        tab_key=tab_key,
        status=status,
        brand=brand,
        cx=cx,
    )


def animated_bounds(layout: Layout) -> list[Rect]:
    """Rectangles that change between frames (aura, keys, status, brand)."""
    pad = 90.0 * layout.scale
    cx0, cy0, cw, ch = layout.card
    sx, sy, sw, sh = layout.space_key
    tx, ty, tw, th = layout.tab_key
    left, top = min(cx0, sx, tx), min(cy0, sy, ty)
    right, bottom = max(cx0+cw, sx+sw, tx+tw), max(cy0+ch, sy+sh, ty+th)
    return [
        (left-pad, top-pad*0.6, right-left+2*pad, bottom-top+1.4*pad),
        _grow(layout.status, 6.0),
        _grow(layout.brand, 6.0),
    ]


def _grow(r: Rect, by: float) -> Rect:
    return (r[0] - by, r[1] - by, r[2] + 2 * by, r[3] + 2 * by)


@dataclass(frozen=True)
class KeyModel:
    key: str            # "SPACE" / "TAB"
    label: str
    sub: str
    mode: str           # idle | busy | armed | dim


def status_label(state_name: str) -> str:
    return {
        "verifying_lock": "VERIFYING",
        "verify_failed": "NOT RECOGNIZED",
        "social_lock": "PRIVACY LOCK",
        "hostile_lock": "SECURITY HOLD",
        "camera_fault": "CAMERA FAULT",
    }.get(state_name, "LOCKED")


def message_for(state_name: str, detail: str = "", tab_armed: bool = False) -> tuple[str, str]:
    """(headline, supporting line) shown beside the clock. Keep the copy short: it sits
    in a narrow column and wraps to at most two lines."""
    detail = (detail or "").strip()
    if tab_armed:
        # Applies in every lock state: handing over to Windows is never a single stray press.
        return "Lock Windows?", "Press TAB again to lock this PC and sign in with your Windows password"
    if state_name == "verifying_lock":
        return "Verifying", "Hold steady and look at the camera"
    if state_name == "verify_failed":
        if "cooldown" in detail.lower():
            return "Try again shortly", detail
        return "Face not recognized", "Press SPACE to try again"
    if state_name == "social_lock":
        return "Privacy lock", "Someone else is in view. SPACE runs an owner check"
    if state_name == "hostile_lock":
        return "Security hold", "Face verification is paused. Use the Windows password"
    if state_name == "camera_fault":
        reason = detail.split(". ")[0].strip().rstrip(".")
        return "Camera unavailable", (reason + "." if reason else "SPACE retries the camera")
    return "Locked", "Press SPACE to verify it's you"


def key_models(state_name: str, detail: str = "", tab_armed: bool = False) -> tuple[KeyModel, KeyModel]:
    """The two glass key blocks under the card, in display order (SPACE, TAB)."""
    if state_name == "verifying_lock":
        space = KeyModel("SPACE", "Verifying", "Looking for you", "busy")
    elif state_name == "camera_fault":
        space = KeyModel("SPACE", "Retry camera", "Reconnect and try again", "idle")
    elif state_name in _VERIFY_ENABLED:
        space = KeyModel("SPACE", "Verify face", "Unlock with your face", "idle")
    elif state_name == "verify_failed":
        space = KeyModel("SPACE", "Try again", "Available in a moment", "dim")
    else:  # hostile_lock and anything else: face verification is not on offer
        space = KeyModel("SPACE", "Unavailable", "Face check is paused", "dim")

    if tab_armed:
        tab = KeyModel("TAB", "Confirm lock", "Press TAB once more", "armed")
    else:
        tab = KeyModel("TAB", "Windows lock", "Press TAB, then confirm", "idle")
    return space, tab
