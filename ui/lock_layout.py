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
    card: Rect
    time_baseline: float
    date_baseline: float
    divider_y: float
    message: Rect
    space_key: Rect
    tab_key: Rect
    status: Rect
    brand: Rect
    cx: float


def compute(x: float, y: float, w: float, h: float) -> Layout:
    """Layout for the primary-screen rectangle ``(x, y, w, h)``."""
    s = max(0.85, min(1.6, h / 900.0))
    margin = max(24.0, min(42.0, w / 48.0))
    cx = x + w / 2.0

    card_w, card_h = 600.0 * s, 292.0 * s
    key_h, key_gap, dock_gap = 64.0 * s, 14.0 * s, 20.0 * s
    group_h = card_h + dock_gap + key_h
    top = y + max(margin * 2.0 + 36.0 * s, (h - group_h) * 0.42)
    card = (cx - card_w / 2.0, top, card_w, card_h)

    key_w = (card_w - key_gap) / 2.0
    key_y = top + card_h + dock_gap
    space_key = (card[0], key_y, key_w, key_h)
    tab_key = (card[0] + key_w + key_gap, key_y, key_w, key_h)

    message_h = 70.0 * s
    message = (card[0] + 36.0 * s, top + card_h - message_h - 22.0 * s, card_w - 72.0 * s, message_h)
    status = (cx - 100.0 * s, y + margin, 200.0 * s, 36.0 * s)
    brand = (cx - 140.0 * s, y + h - margin - 24.0 * s, 280.0 * s, 24.0 * s)
    return Layout(
        scale=s,
        card=card,
        time_baseline=top + 118.0 * s,
        date_baseline=top + 164.0 * s,
        divider_y=top + card_h - message_h - 34.0 * s,
        message=message,
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
    sx, sy, sw, _sh = layout.space_key
    _tx, _ty, tw, th = layout.tab_key
    return [
        (cx0 - pad, cy0 - pad * 0.6, cw + pad * 2.0, (sy + th) - cy0 + pad * 1.4),
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
    """(headline, supporting line) shown at the foot of the clock card."""
    detail = (detail or "").strip()
    if state_name == "verifying_lock":
        return "Verifying", "Hold steady and look at the camera"
    if state_name == "verify_failed":
        if "cooldown" in detail.lower():
            return "Try again shortly", detail
        return "Face not recognized", "Press SPACE to try again"
    if state_name == "social_lock":
        return "Privacy lock", "Someone else is in view. SPACE runs an owner check"
    if state_name == "hostile_lock":
        return "Security hold", "Face verification is paused. TAB uses the Windows password"
    if state_name == "camera_fault":
        if tab_armed:
            return "Press TAB again", "Confirm to continue with the Windows password"
        headline = (detail.split(". ")[0] or "Camera unavailable").strip()
        return headline, "SPACE retries the camera. TAB twice uses the Windows password"
    return "Locked", "Press SPACE to verify it's you"


def key_models(state_name: str, detail: str = "", tab_armed: bool = False) -> tuple[KeyModel, KeyModel]:
    """The two key caps under the card, in display order (SPACE, TAB)."""
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

    if state_name == "camera_fault":
        if tab_armed:
            tab = KeyModel("TAB", "Confirm now", "Press TAB once more", "armed")
        else:
            tab = KeyModel("TAB", "Windows password", "Press twice to confirm", "idle")
    else:
        tab = KeyModel("TAB", "Windows password", "Use your PIN or password", "idle")
    return space, tab
