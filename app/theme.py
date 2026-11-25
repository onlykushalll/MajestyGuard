"""Colour themes for the Control Center.

One frozen ``Theme`` per preset; widgets read the active one at paint time and the
stylesheet is built from it, so switching is instant and nothing is hard-coded in
the window. Pure data + a tiny JSON preference file (no Qt import).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    tagline: str
    dark: bool
    bg_top: str
    bg_bottom: str
    glow_a: str          # large soft glow, top-right
    glow_b: str          # large soft glow, bottom-left
    surface: str
    surface_hi: str      # raised / hover
    border: str          # rgba string
    text: str
    text_soft: str
    text_faint: str
    accent_a: str        # gradient start
    accent_b: str        # gradient end
    on_accent: str       # text on top of the accent gradient
    ok: str = "#2ED47A"
    warn: str = "#FFB020"
    bad: str = "#FF5C6C"
    info: str = "#5B8CFF"


MIDNIGHT = Theme(
    "midnight", "Midnight", "Blue to violet", True,
    "#0A0C14", "#10131F", "#5B3DF5", "#1F6BFF",
    "#141828", "#1B2036", "rgba(255,255,255,0.07)",
    "#F3F5FB", "#A9B2C9", "#7E88A3",
    "#4F7CFF", "#9B5CFF", "#FFFFFF",
)
AURORA = Theme(
    "aurora", "Aurora", "Green to cyan", True,
    "#061110", "#0A1B19", "#12C4A0", "#1E7BFF",
    "#0E1D1B", "#142826", "rgba(255,255,255,0.07)",
    "#EAFBF6", "#9BBFB6", "#6F9389",
    "#19D6A2", "#2AA8FF", "#04120F",
)
EMBER = Theme(
    "ember", "Ember", "Orange to pink", True,
    "#110A0C", "#1A0E11", "#FF5A3C", "#FF2D87",
    "#211318", "#2B1A20", "rgba(255,255,255,0.07)",
    "#FFF2EE", "#D1ADA8", "#A07F7A",
    "#FF7A45", "#FF3D81", "#FFFFFF",
)
DAYLIGHT = Theme(
    "daylight", "Daylight", "Light, blue to violet", False,
    "#F5F7FC", "#E8ECF6", "#8A63FF", "#3B8BFF",
    "#FFFFFF", "#F1F4FB", "rgba(20,28,50,0.09)",
    "#141A2A", "#4B5670", "#6B7590",
    "#3B6BFF", "#8A4DFF", "#FFFFFF",
    ok="#12A150", warn="#C77700", bad="#D92D45", info="#2F62F0",
)

PRESETS = (MIDNIGHT, AURORA, EMBER, DAYLIGHT)
DEFAULT = MIDNIGHT
_BY_KEY = {t.key: t for t in PRESETS}

_active = DEFAULT


def active() -> Theme:
    return _active


def set_active(theme: Theme) -> Theme:
    global _active
    _active = theme
    return _active


def by_key(key: str) -> Theme:
    return _BY_KEY.get(str(key), DEFAULT)


def load_choice(path: Path) -> Theme:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return by_key(data.get("theme", "")) if isinstance(data, dict) else DEFAULT
    except (OSError, ValueError):
        return DEFAULT


def save_choice(path: Path, theme: Theme) -> bool:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"theme": theme.key}), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def build_style(t: Theme) -> str:
    """The whole window stylesheet for a theme."""
    font = "'Segoe UI Variable', 'Segoe UI'"
    return f"""
QMainWindow {{ background: {t.bg_top}; color: {t.text}; }}
QLabel {{ font-family: {font}; color: {t.text}; background: transparent; }}
QLabel#eyebrow {{ color: {t.accent_a}; font: 700 10px {font}; letter-spacing: 1.8px; }}
QLabel#brand {{ font: 700 15px {font}; }}
QLabel#brandSub {{ color: {t.text_soft}; font: 500 11px {font}; }}
QLabel#pageTitle {{ font: 700 30px {font}; }}
QLabel#pageSub {{ color: {t.text_soft}; font: 400 13px {font}; }}
QLabel#cardTitle {{ font: 600 15px {font}; }}
QLabel#heroTitle {{ color: #FFFFFF; font: 700 26px {font}; }}
QLabel#heroSub {{ color: rgba(255,255,255,0.84); font: 400 13px {font}; }}
QLabel#detail {{ color: {t.text_soft}; font: 400 12px {font}; }}
QLabel#value {{ font: 700 22px {font}; }}
QLabel#statLabel {{ color: {t.text_faint}; font: 700 10px {font}; letter-spacing: 1.3px; }}
QLabel#rowTitle {{ font: 600 13px {font}; }}
QLabel#rowMeta {{ color: {t.text_soft}; font: 400 11px {font}; }}
QLabel#mono {{ color: {t.text_soft}; font: 500 11px 'Cascadia Mono', Consolas, monospace; }}
QLabel#chip {{ background: rgba(255,255,255,0.20); color: #FFFFFF; border: 1px solid rgba(255,255,255,0.28);
  border-radius: 13px; padding: 4px 12px; font: 600 11px {font}; }}
QLabel#pill {{ background: {t.surface_hi}; color: {t.text}; border: 1px solid {t.border};
  border-radius: 11px; padding: 3px 11px; font: 600 11px {font}; }}
QLabel#keycap {{ background: {t.surface_hi}; color: {t.text}; border: 1px solid {t.border};
  border-bottom: 2px solid {t.border}; border-radius: 8px; padding: 4px 10px; font: 700 11px {font}; }}
QFrame#hair {{ background: {t.border}; max-height: 1px; min-height: 1px; border: 0; }}
QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent_a}, stop:1 {t.accent_b});
  color: {t.on_accent}; border: 0; border-radius: 14px; padding: 12px 22px; font: 700 13px {font}; }}
QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent_b}, stop:1 {t.accent_a}); }}
QPushButton:focus {{ border: 2px solid {t.text}; }}
QPushButton:disabled {{ background: {t.surface_hi}; color: {t.text_faint}; }}
QPushButton#hero {{ background: #FFFFFF; color: #11151F; border-radius: 15px; padding: 13px 24px; font: 700 13px {font}; }}
QPushButton#hero:hover {{ background: #F0F3FA; }}
QPushButton#hero:disabled {{ background: rgba(255,255,255,0.35); color: rgba(17,21,31,0.5); }}
QPushButton#secondary {{ background: {t.surface_hi}; color: {t.text}; border: 1px solid {t.border};
  border-radius: 12px; padding: 9px 18px; font: 600 12px {font}; }}
QPushButton#secondary:hover {{ border: 1px solid {t.accent_a}; }}
QPushButton#secondary:focus {{ border: 1.5px solid {t.accent_a}; }}
QPushButton#secondary:disabled {{ color: {t.text_faint}; background: transparent; }}
QPushButton#secondary[current="true"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent_a},
  stop:1 {t.accent_b}); color: {t.on_accent}; border: 1px solid transparent; }}
QPushButton#link {{ background: transparent; color: {t.accent_a}; border: 0; padding: 4px 6px; font: 700 12px {font}; }}
QPushButton#link:hover {{ color: {t.text}; }}
QSpinBox {{ background: {t.surface_hi}; color: {t.text}; border: 1px solid {t.border};
  border-radius: 12px; padding: 7px 12px; font: 600 12px {font}; selection-background-color: {t.accent_a}; }}
QSpinBox:focus {{ border: 1px solid {t.accent_a}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QSpinBox:disabled {{ color: {t.text_faint}; }}
"""
