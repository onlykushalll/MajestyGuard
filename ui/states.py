"""
Dynamic Island state definitions.

The daemon only needs to send a state name and optional numeric fields. The UI
maps that payload to a compact visual presentation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PIPE_NAME = r"\\.\pipe\MajestyGuard_UI"
COMMAND_PIPE_NAME = r"\\.\pipe\MajestyGuard_CMD"


@dataclass
class IslandState:
    name: str
    width: int
    height: int
    bg_color: str
    border_color: str
    accent_color: str
    label: str
    label_color: str
    mode: str = "pill"
    pulse: bool = False
    flash: bool = False
    confidence: Optional[float] = None
    liveness: Optional[float] = None
    progress: Optional[float] = None
    quality: Optional[float] = None
    face_position: Optional[float] = None
    detail: str = ""


STATES: dict[str, IslandState] = {
    "idle": IslandState(
        name="idle",
        width=120,
        height=28,
        bg_color="#111111",
        border_color="#1C1C1E",
        accent_color="#343438",
        label="",
        label_color="#77777C",
    ),
    "scanning": IslandState(
        name="scanning",
        width=160,
        height=34,
        bg_color="#0A0A0A",
        border_color="#3A2F1F",
        accent_color="#FFB340",
        label="",
        label_color="#E7FFEC",
        mode="dot_scan",
        pulse=True,
    ),
    "active": IslandState(
        name="active",
        width=160,
        height=34,
        bg_color="#030303",
        border_color="#1F4A2A",
        accent_color="#34C759",
        label="",
        label_color="#B9F6C8",
        mode="verified",
    ),
    "welcome": IslandState(
        name="welcome",
        width=168,
        height=34,
        bg_color="#0D1F0D",
        border_color="#1A3A1A",
        accent_color="#34C759",
        label="Welcome",
        label_color="#FFFFFF",
        mode="welcome",
    ),
    "stranger": IslandState(
        name="stranger",
        width=220,
        height=54,
        bg_color="#030303",
        border_color="#4A1515",
        accent_color="#FF453A",
        label="Unknown face",
        label_color="#FFD0CC",
        mode="failure",
        flash=True,
    ),
    "locked": IslandState(
        name="locked",
        width=118,
        height=12,
        bg_color="#030303",
        border_color="#211010",
        accent_color="#663333",
        label="",
        label_color="#553333",
    ),
    "soft_locked": IslandState(
        name="soft_locked",
        width=286,
        height=66,
        bg_color="#020406",
        border_color="#2B3A4A",
        accent_color="#64D2FF",
        label="Locked",
        label_color="#EAF7FF",
        mode="shield",
        pulse=True,
    ),
    "locked_passive": IslandState(
        name="locked_passive",
        width=286,
        height=66,
        bg_color="#020406",
        border_color="#3A2F1F",
        accent_color="#FFB340",
        label="Locked",
        label_color="#FFE3B0",
        mode="shield",
        pulse=True,
    ),
    "verifying_lock": IslandState(
        name="verifying_lock",
        width=206,
        height=72,
        bg_color="#020406",
        border_color="#25282B",
        accent_color="#34C759",
        label="Verifying",
        label_color="#E7FFEC",
        mode="face_scan",
        pulse=True,
    ),
    "social_lock": IslandState(
        name="social_lock",
        width=318,
        height=68,
        bg_color="#050303",
        border_color="#473015",
        accent_color="#FFB340",
        label="Privacy lock",
        label_color="#FFE3B0",
        mode="shield",
        pulse=True,
    ),
    "hostile_lock": IslandState(
        name="hostile_lock",
        width=326,
        height=70,
        bg_color="#050202",
        border_color="#4A1515",
        accent_color="#FF453A",
        label="Security lock",
        label_color="#FFD0CC",
        mode="shield",
        pulse=True,
        flash=True,
    ),
    "verify_failed": IslandState(
        name="verify_failed",
        width=286,
        height=66,
        bg_color="#050202",
        border_color="#4A1515",
        accent_color="#FF453A",
        label="",
        label_color="#FFD0CC",
        mode="verify_fail",
        pulse=False,
    ),
    "enrolling": IslandState(
        name="enrolling",
        width=312,
        height=52,
        bg_color="#030303",
        border_color="#24272E",
        accent_color="#64D2FF",
        label="Enrollment",
        label_color="#E8F7FF",
        mode="enrollment",
        pulse=True,
    ),
    "calibrating": IslandState(
        name="calibrating",
        width=286,
        height=54,
        bg_color="#030303",
        border_color="#1D2730",
        accent_color="#64D2FF",
        label="Calibrating",
        label_color="#E8F7FF",
        mode="diagnostic",
        pulse=True,
    ),
    "exit": IslandState(
        name="exit",
        width=120,
        height=28,
        bg_color="#111111",
        border_color="#1C1C1E",
        accent_color="#343438",
        label="Shutting down",
        label_color="#77777C",
    ),
}


def _clamp_optional(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def get_state(
    name: str,
    confidence: Optional[float] = None,
    liveness: Optional[float] = None,
    progress: Optional[float] = None,
    quality: Optional[float] = None,
    face_position: Optional[float] = None,
    detail: str = "",
) -> IslandState:
    """Return a copy of the named state, with optional live metrics merged in."""
    base = STATES.get(name, STATES["idle"])
    state = IslandState(**base.__dict__)
    state.confidence = _clamp_optional(confidence)
    state.liveness = _clamp_optional(liveness)
    state.progress = _clamp_optional(progress)
    state.quality = _clamp_optional(quality)
    state.face_position = _clamp_optional(face_position)
    state.detail = detail or ""

    if name in {"soft_locked", "locked_passive"}:
        state.label = "Locked"
    elif name == "verifying_lock":
        state.label = "Verifying"
    elif name == "social_lock":
        state.label = "Privacy lock"
    elif name == "hostile_lock":
        state.label = "Security lock"
    elif name == "verify_failed":
        state.label = ""
    elif name == "enrolling":
        pct = state.progress * 100 if state.progress is not None else 0.0
        state.label = f"Enrollment {pct:.0f}%"
    elif name == "calibrating":
        state.label = "Face quality"
    return state
