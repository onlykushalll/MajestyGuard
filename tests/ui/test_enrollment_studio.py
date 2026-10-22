"""Offscreen unit and render tests for the Apple Face ID-inspired EnrollmentStudioWindow."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
if str(UI) not in sys.path:
    sys.path.insert(0, str(UI))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from enrollment_window import (  # noqa: E402
    EnrollmentStudioWindow,
    FaceIdRingCanvas,
    check_pose_alignment,
    estimate_head_pose_5pt,
    target_pose_for_angle,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_estimate_head_pose_5pt_frontal_and_turned():
    frontal_kps = np.array(
        [
            [40.0, 50.0],
            [80.0, 50.0],
            [60.0, 72.0],
            [44.0, 92.0],
            [76.0, 92.0],
        ],
        dtype=np.float32,
    )
    yaw, pitch, roll = estimate_head_pose_5pt(frontal_kps)
    assert abs(yaw) < 0.08
    assert abs(pitch) < 0.18
    assert abs(roll) < 1.0

    # Nose shifted right in image -> negative yaw in mirrored selfie coordinates
    turned_kps = frontal_kps.copy()
    turned_kps[2, 0] = 72.0
    yaw_turned, _, _ = estimate_head_pose_5pt(turned_kps, mirror_yaw=True)
    assert yaw_turned < -0.35


def test_target_pose_and_alignment_matching():
    ty, tp, _tol = target_pose_for_angle("Left 25 degrees - stronger turn")
    assert ty < -0.4
    assert tp == 0.0

    aligned, _ = check_pose_alignment(-0.55, 0.0, "Left 25 degrees - stronger turn")
    assert aligned is True

    misaligned, _ = check_pose_alignment(0.55, 0.0, "Left 25 degrees - stronger turn")
    assert misaligned is False


def test_face_id_ring_canvas_renders_visible_ring_and_reticle(qapp):
    canvas = FaceIdRingCanvas()
    canvas.resize(380, 380)
    frame = np.full((240, 320, 3), 40, dtype=np.uint8)
    canvas.set_frame_state(
        frame,
        yaw=-0.25,
        pitch=0.10,
        target_yaw=-0.32,
        target_pitch=0.0,
        ready=True,
        aligned=True,
        dwell_progress=0.65,
        overall_progress=0.50,
    )

    pixmap = QPixmap(canvas.size())
    pixmap.fill(QColor("#080B11"))
    canvas.render(pixmap)
    image = pixmap.toImage()
    bg = QColor("#080B11").rgb()
    non_bg = sum(
        1
        for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
        if image.pixel(x, y) != bg
    )
    assert non_bg > 50


def test_enrollment_studio_window_burst_progression(qapp):
    angles = ["Front - neutral expression", "Left 10 degrees - small turn"]
    captured_calls = []

    def fake_burst(angle: str, idx: int):
        captured_calls.append((angle, idx))
        return [f"sample-{idx}-1", f"sample-{idx}-2"]

    win = EnrollmentStudioWindow(
        angles=angles,
        profile="quick",
        samples_per_angle=2,
        on_capture_burst=fake_burst,
    )
    assert win.current_angle() == angles[0]
    win.trigger_capture()
    assert win.current_angle() == angles[1]
    assert win.completed is False

    win.trigger_capture()
    assert win.completed is True
    assert len(win.captured_samples) == 4
    assert captured_calls == [(angles[0], 1), (angles[1], 2)]


def test_enrollment_studio_public_abort_and_profile_switch(qapp):
    angles_quick = ["Front", "Left"]
    angles_real = ["Front", "Left", "Night lying down"]
    win = EnrollmentStudioWindow(
        angles=angles_quick,
        profile="quick",
        samples_per_angle=2,
        on_switch_profile=lambda p: angles_real if p == "real-life" else angles_quick,
    )
    win._handle_profile_change("real-life")
    assert win.selected_profile == "real-life"
    assert len(win.active_angles) == 3

    win.abort()
    assert win.aborted is True
