import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))

from states import get_state  # noqa: E402


def test_soft_lock_states_exist_for_ipc_and_ui():
    scanning = get_state("scanning")
    passive = get_state("locked_passive", detail="idle_timeout")
    soft = get_state("soft_locked", detail="idle_timeout")
    verifying = get_state("verifying_lock")
    active = get_state("active")
    stranger = get_state("stranger")
    social = get_state("social_lock")
    hostile = get_state("hostile_lock")

    assert scanning.mode == "dot_scan"
    assert scanning.label == ""
    assert scanning.accent_color == "#FFB340"
    assert passive.mode == "shield"
    assert passive.label == "Locked"
    assert passive.accent_color == "#FFB340"
    assert passive.pulse is True
    assert soft.mode == "shield"
    assert soft.label == "Locked"
    assert verifying.mode == "face_scan"
    assert verifying.label == "Verifying"
    assert verifying.accent_color == "#34C759"
    assert active.mode == "verified"
    assert stranger.mode == "failure"
    assert social.mode == "shield"
    assert hostile.mode == "shield"


def test_soft_lock_overlay_source_is_fullscreen_glass_input_shield():
    text = (UI / "soft_lock.py").read_text(encoding="utf-8")

    assert "SoftLockOverlay" in text
    assert "WindowStaysOnTopHint" in text
    assert "FramelessWindowHint" in text
    assert "showFullScreen" in text
    assert "SetWindowPos" in text
    assert "WindowDeactivate" in text
    assert "WindowStateChange" in text
    assert "grabWindow(0)" in text
    assert "WindowTransparentForInput" not in text
    assert "#F5F7FA" not in text
    assert "Secured by MajestyGuard" in text
    assert "_paint_corner_status" in text
    assert "_paint_brand_signature" in text
    assert "QColor(246, 248, 252" in text
    assert "QColor(82, 86, 94" in text
    assert "_build_noise_texture" in text
    assert "_paint_noise_texture" in text
    assert "drawTiledPixmap" in text
    assert "MajestyGuard locked" not in text
    assert "Face verification required to resume this desktop" not in text
    assert "drawLine(x, 0, x + rect.height()" not in text
    assert "QLinearGradient(0, 0, rect.width(), rect.height())" not in text
    assert "QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())" not in text
    assert "glass" in text.lower()
    assert "blur" in text.lower()
    assert "_request_verification" in text
    assert "overlay_key" in text
    assert "overlay_click" in text


def test_dynamic_island_can_request_verification_when_locked():
    text = (UI / "island.py").read_text(encoding="utf-8")
    main = (UI / "main.py").read_text(encoding="utf-8")

    assert "WindowTransparentForInput" not in text
    assert "_VERIFY_REQUEST_STATES" in text
    assert "island_click" in text
    assert "COMMAND_PIPE_NAME" in main
    assert "verify_requested" in main
    assert "WaitNamedPipeW" in main


def test_face_scan_visual_has_face_id_glyph():
    text = (UI / "island.py").read_text(encoding="utf-8")

    assert "_paint_face_id_glyph" in text
    assert "_paint_face_id_trace" in text
    assert "_paint_biometric_material" in text
    assert "_paint_scan_rail" in text
    assert "setDashOffset" in text
    assert "setDashPattern" in text
    assert "drawArc(edge" not in text
    assert "_paint_face_scan_orbits" not in text
    assert "_paint_face_scan_brackets" not in text
    assert "scan_y =" not in text
    assert "top_left.cubicTo" in text
    assert "_glyph_rect" in text
    assert "smile.cubicTo" in text


@pytest.mark.parametrize("state", ["locked_passive", "soft_locked", "social_lock", "hostile_lock"])
def test_shield_states_are_not_tiny_minimized_pills(state):
    visual = get_state(state)

    assert 280 <= visual.width <= 340
    assert 64 <= visual.height <= 74


@pytest.mark.parametrize("state", ["verifying_lock"])
def test_face_scan_states_use_compact_biometric_capsule(state):
    visual = get_state(state)

    assert visual.mode == "face_scan"
    assert 190 <= visual.width <= 212
    assert 68 <= visual.height <= 76
    assert visual.width > visual.height
    assert visual.label in {"Scanning", "Verifying"}


def test_verified_state_is_medium_success_pill():
    visual = get_state("active", confidence=0.96)

    assert visual.mode == "verified"
    assert 150 <= visual.width <= 170
    assert visual.label == ""


def test_shield_states_use_centered_status_painter():
    text = (UI / "island.py").read_text(encoding="utf-8")

    assert "_paint_shield" in text
    assert "Click or Space" in text
    assert "Owner only" in text
    assert "Windows lock" in text
