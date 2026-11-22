"""Design tokens shared by the lock overlay, the island and the Control Center.

One place for colour, radius, spacing and motion values so the three surfaces
read as one product. Pure data (no Qt import) so it is cheap to import from
anywhere and trivial to test.
"""
from __future__ import annotations

# --- ink on light glass (R, G, B) -------------------------------------------
INK = (24, 28, 38)
INK_SOFT = (58, 64, 78)
INK_FAINT = (96, 104, 120)

# --- semantic accents (Apple system colours, light appearance) --------------
BLUE = (10, 132, 255)
CYAN = (50, 173, 230)
GREEN = (48, 180, 78)
AMBER = (255, 149, 0)
RED = (255, 69, 58)

ACCENT_BY_STATE = {
    "locked_passive": CYAN,
    "soft_locked": CYAN,
    "verifying_lock": GREEN,
    "social_lock": AMBER,
    "hostile_lock": RED,
    "verify_failed": RED,
    "camera_fault": AMBER,
}


def accent_for(state_name: str) -> tuple[int, int, int]:
    return ACCENT_BY_STATE.get(state_name, BLUE)


# --- shape -------------------------------------------------------------------
RADIUS_CARD = 44.0
RADIUS_PILL = 999.0     # callers clamp to height / 2
SPACE_UNIT = 4

# --- motion (milliseconds) ---------------------------------------------------
MOTION_FAST = 120
MOTION_BASE = 220
MOTION_SLOW = 420
EASE_OUT = "OutCubic"


def hex_of(rgb: tuple[int, int, int]) -> str:
    """'#RRGGBB' for a token colour (Qt stylesheets and QColor(str))."""
    return "#%02X%02X%02X" % tuple(int(c) for c in rgb)


def shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darken (<1) or lighten (>1) a token colour; used for hover/pressed states."""
    return tuple(max(0, min(255, int(round(c * factor)))) for c in rgb)  # type: ignore[return-value]
