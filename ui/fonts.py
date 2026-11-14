"""One place for every UI font.

On Windows 11 the family that Qt can actually resolve is "Segoe UI Variable"
(a variable font with weight and optical-size axes). The old names
"Segoe UI Variable Display" / "Segoe UI Variable Text" are *not* resolved by
Qt on a normal install and silently fall back to Tahoma, so every label in the
overlay and the island was drawn in Tahoma.

``ui_font`` never raises: any problem with the variable-font calls leaves a
plain, still-correct QFont.
"""
from __future__ import annotations

from PyQt6.QtGui import QFont

FAMILIES = ["Segoe UI Variable", "Segoe UI", "Arial"]


def ui_font(
    size: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    tabular: bool = False,
    spacing: float | None = None,
) -> QFont:
    """Segoe UI Variable at ``size`` pt with the optical size matched to it
    (8..36 pt, as the font defines) and optional tabular figures."""
    font = QFont(FAMILIES[0], int(round(size)))
    try:
        font.setFamilies(FAMILIES)
        font.setPointSizeF(float(size))
        font.setWeight(weight)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        font.setVariableAxis(QFont.Tag("opsz"), max(8.0, min(36.0, float(size))))
    except Exception:  # older Qt / non-variable fallback family
        pass
    if tabular:
        try:
            font.setFeature(QFont.Tag("tnum"), 1)
        except Exception:
            pass
    if spacing is not None:
        try:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(spacing))
        except Exception:
            pass
    return font
