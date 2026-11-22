"""Offscreen unit and render tests for the Apple Face ID-inspired EnrollmentStudioWindow."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
if str(UI) not in sys.path:
    sys.path.insert(0, str(UI))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
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


def _wait_for(qapp, predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return predicate()


def _set_ready(win):
    kps = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    angle = win.current_angle().lower()
    if "left 10" in angle:
        kps[2, 0] = 66.0
    elif "right 10" in angle:
        kps[2, 0] = 54.0
    win.update_live_frame(
        np.zeros((120, 160, 3), dtype=np.uint8),
        SimpleNamespace(kps=kps),
        ready=True,
        status_text="READY",
        quality_score=0.9,
    )


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


def test_space_cannot_capture_until_the_requested_pose_is_aligned(qapp):
    captured = []
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    win = EnrollmentStudioWindow(
        angles=["Right 10 degrees - small turn"],
        profile="quick",
        samples_per_angle=1,
        on_capture_burst=lambda angle, idx: captured.append((angle, idx)) or ["sample"],
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    win.update_live_frame(
        frame,
        SimpleNamespace(kps=frontal),
        ready=True,
        status_text="READY",
        quality_score=0.9,
    )

    win.trigger_capture()
    qapp.processEvents()

    assert captured == []
    assert win.capture_in_progress is False
    assert "right" in win._status_chip.text().lower()
    win.close()


def test_front_view_auto_captures_without_a_manual_button_press(qapp):
    captured = []
    # This landmark set has deliberately noisy pitch. A centered baseline view
    # should not ask the user to hit a button or hold a mathematically exact pose.
    kps = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 88.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    win = EnrollmentStudioWindow(
        angles=["Front - neutral expression"],
        profile="quick",
        samples_per_angle=1,
        on_capture_burst=lambda angle, idx: captured.append((angle, idx)) or ["sample"],
    )
    win._aligned_since = time.monotonic() - 1.0

    win.update_live_frame(
        np.zeros((120, 160, 3), dtype=np.uint8),
        SimpleNamespace(kps=kps),
        ready=True,
        status_text="READY",
        quality_score=0.9,
    )

    assert _wait_for(qapp, lambda: win.completed)
    assert captured == [("Front - neutral expression", 1)]


def test_preview_keeps_updating_while_face_analysis_is_busy(qapp):
    entered = threading.Event()
    release = threading.Event()

    def slow_analysis(_frame):
        entered.set()
        release.wait(1.0)
        return None, False, "no face", 0.0

    win = EnrollmentStudioWindow(angles=["Front"], profile="quick")
    analysis_frame = np.zeros((120, 160, 3), dtype=np.uint8)
    latest_preview = np.zeros((120, 160, 3), dtype=np.uint8)
    latest_preview[:, :, 2] = 220
    win.analyze_live_frame_async(analysis_frame, slow_analysis)
    try:
        assert entered.wait(0.5)
        win.update_preview_frame(latest_preview)

        assert win.analysis_in_progress is True
        assert win.canvas._frame_image.pixelColor(20, 20).red() == 220
        assert not np.any(latest_preview)
    finally:
        release.set()
        win.close()


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
    _set_ready(win)
    win.trigger_capture()
    assert win.capture_in_progress is True
    assert _wait_for(qapp, lambda: not win.capture_in_progress)
    assert win.current_angle() == angles[1]
    assert win.completed is False

    _set_ready(win)
    win.trigger_capture()
    assert _wait_for(qapp, lambda: win.completed)
    assert win.completed is True
    assert len(win.captured_samples) == 4
    assert captured_calls == [(angles[0], 1), (angles[1], 2)]


def test_capture_burst_runs_without_blocking_qt_event_loop(qapp):
    entered = threading.Event()
    release = threading.Event()
    timer_fired = []

    def slow_burst(_angle: str, _idx: int):
        entered.set()
        release.wait(1.0)
        return ["sample-1", "sample-2"]

    win = EnrollmentStudioWindow(
        angles=["Front"],
        profile="quick",
        samples_per_angle=2,
        on_capture_burst=slow_burst,
    )
    QTimer.singleShot(0, lambda: timer_fired.append(True))
    try:
        _set_ready(win)
        win.trigger_capture()
        assert entered.wait(0.5)
        assert win.capture_in_progress is True
        qapp.processEvents()
        assert timer_fired == [True]

        release.set()
        assert _wait_for(qapp, lambda: win.completed)
        assert win.completed is True
        assert win.captured_samples == ["sample-1", "sample-2"]
    finally:
        release.set()
        win.close()


def test_live_analysis_runs_off_ui_thread_and_keeps_event_loop_responsive(qapp):
    entered = threading.Event()
    release = threading.Event()
    timer_fired = []
    analysis_threads = []
    ui_thread = threading.get_ident()

    def slow_analysis(_frame):
        analysis_threads.append(threading.get_ident())
        entered.set()
        release.wait(1.0)
        return None, False, "no face", 0.0

    win = EnrollmentStudioWindow(angles=["Front"], profile="quick")
    frame = np.full((120, 160, 3), 32, dtype=np.uint8)
    QTimer.singleShot(0, lambda: timer_fired.append(True))
    try:
        win.analyze_live_frame_async(frame, slow_analysis)
        assert entered.wait(0.5)
        assert win.analysis_in_progress is True
        qapp.processEvents()
        assert timer_fired == [True]

        release.set()
        assert _wait_for(qapp, lambda: not win.analysis_in_progress)
        assert analysis_threads and analysis_threads[0] != ui_thread
        assert win._status_chip.text() == "Move into the camera frame."
        assert not np.any(frame)
    finally:
        release.set()
        win.close()


def test_abort_during_live_analysis_waits_for_worker_and_zeroes_frame(qapp):
    entered = threading.Event()
    release = threading.Event()
    frame = np.full((120, 160, 3), 80, dtype=np.uint8)

    def slow_analysis(_frame):
        entered.set()
        release.wait(1.0)
        return None, False, "no face", 0.0

    win = EnrollmentStudioWindow(angles=["Front"], profile="quick")
    win.analyze_live_frame_async(frame, slow_analysis)
    try:
        assert entered.wait(0.5)
        win.abort()
        assert win.analysis_in_progress is True
        assert win.aborted is False

        release.set()
        assert _wait_for(qapp, lambda: win.aborted)
        assert not np.any(frame)
    finally:
        release.set()
        win.close()


def test_window_close_records_a_specific_abort_reason(qapp):
    win = EnrollmentStudioWindow(angles=["Front"], profile="quick")

    win.close()

    assert win.aborted is True
    assert win.abort_reason == "window_closed"


def test_enrollment_window_shows_with_usable_geometry(qapp):
    win = EnrollmentStudioWindow(angles=["Front"], profile="quick")
    win.show()
    qapp.processEvents()

    try:
        assert win.isVisible()
        assert win.width() >= 520
        assert win.height() >= 600
        assert win.canvas.isVisible()
        assert win._progress_bar.isVisible()
    finally:
        win.close()
        qapp.processEvents()


def test_abort_during_capture_defers_close_and_discards_samples(qapp):
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def burst(_angle: str, idx: int):
        nonlocal calls
        calls += 1
        if idx == 2:
            entered.set()
            release.wait(0.5)
        return [f"sample-{idx}-1", f"sample-{idx}-2"]

    win = EnrollmentStudioWindow(
        angles=["Front", "Left"],
        profile="quick",
        samples_per_angle=2,
        on_capture_burst=burst,
    )
    try:
        _set_ready(win)
        win.trigger_capture()
        assert _wait_for(qapp, lambda: not win.capture_in_progress)
        assert len(win.captured_samples) == 2

        _set_ready(win)
        win.trigger_capture()
        assert entered.wait(0.5)
        win.abort()
        assert win.capture_in_progress is True
        assert win.aborted is False

        release.set()
        assert _wait_for(qapp, lambda: win.aborted)
        assert win.captured_samples == []
    finally:
        release.set()
        win.close()


def test_capture_callback_error_leaves_studio_retryable(qapp):
    def broken_burst(_angle: str, _idx: int):
        raise RuntimeError("model error")

    win = EnrollmentStudioWindow(
        angles=["Front"],
        profile="quick",
        samples_per_angle=2,
        on_capture_burst=broken_burst,
    )
    _set_ready(win)
    win.trigger_capture()
    assert _wait_for(qapp, lambda: not win.capture_in_progress)
    assert win.completed is False
    assert win.aborted is False
    assert "Camera check failed" in win._status_chip.text()
    assert not win._capture_btn.isEnabled()
    win.close()


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
