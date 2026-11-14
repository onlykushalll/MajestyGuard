from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "daemon"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import enrollment_coverage as ec  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def win(qapp):
    import coverage_enrollment_window as cew

    w = cew.CoverageEnrollmentWindow()
    w.resize(520, 700)
    yield w
    w.finished = True
    w.close()


def fb(state=ec.COLLECTING, progress=0.2, **kw):
    return ec.Feedback(state=state, progress=progress, **kw)


def _preview():
    from PyQt6.QtGui import QColor, QImage

    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 70, 60))
    return img


def _ring_pixel(win, fill, state=ec.COLLECTING):
    ring = win.ring
    ring.resize(500, 500)
    ring.set_feedback(fb(state=state, fill=fill))
    img = ring.grab().toImage()
    r = 500 / 2.0 - 10.0
    rad = r * 0.90                      # outer layer
    x, y = int(250 - rad), 250          # left_wide arc is centred on 180 degrees
    return img.pixelColor(x, y)


def test_every_state_paints_without_error(win):
    win.ring.set_preview(_preview())
    for f in (
        fb(ec.CALIBRATING, 0.0, calibration=0.5, reason="calibrating"),
        fb(ec.COLLECTING, 0.4, cue="left", target="left_mild", fill={"left_mild": 0.5, "center": 1.0}),
        fb(ec.COLLECTING, 0.6, cue="center", target="center", accepted=True),
        fb(ec.COMPLETE, 1.0),
    ):
        win.apply_feedback(f)
        assert not win.grab().isNull()
    win.show_result(True, "done")
    assert not win.grab().isNull()
    win.show_result(False, "not saved")
    assert not win.grab().isNull()


def test_filled_arc_is_visibly_lit_and_green_at_full(win):
    empty = _ring_pixel(win, {})
    full = _ring_pixel(win, {"left_wide": 1.0})
    assert full.green() > empty.green() + 40
    assert full.green() > full.red()        # blue->green fill, not the white track


def test_cue_and_reason_text(win):
    win.apply_feedback(fb(cue="left", target="left_mild"))
    assert "left" in win.title.text().lower()
    win.apply_feedback(fb(cue="up_right", accepted=False, reason="liveness"))
    assert "right" in win.title.text().lower()
    assert win.hint.text() == "Keep your face uncovered and facing the screen"
    win.apply_feedback(fb(cue="center", accepted=True))
    assert win.hint.text() == ""
    assert win.percent.text() == "20%"


def test_calibration_copy_and_no_percent(win):
    win.apply_feedback(fb(ec.CALIBRATING, 0.0, reason="hold_still"))
    assert win.percent.text() == ""
    assert "Hold still" in win.hint.text()


def test_escape_button_and_close_request_cancel(win):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    hits = []
    win.cancel_requested.connect(lambda: hits.append(1))
    win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    win.cancel_button.click()
    win.close()
    assert len(hits) == 3


def test_finished_window_ignores_updates_and_close_does_not_cancel(win):
    hits = []
    win.cancel_requested.connect(lambda: hits.append(1))
    win.show_result(True, "ok")
    win.apply_feedback(fb(cue="left"))
    assert win.title.text() == "Face enrolled"
    win.close()
    assert hits == []


def test_signals_from_worker_thread_reach_the_gui_thread(win, qapp):
    seen_thread = []
    orig = win.apply_feedback

    def spy(f):
        seen_thread.append(threading.current_thread() is threading.main_thread())
        orig(f)

    win.feedback_signal.disconnect()
    win.feedback_signal.connect(spy)
    t = threading.Thread(target=lambda: win.feedback_signal.emit(fb(cue="down", target="down")))
    t.start()
    t.join()
    for _ in range(20):
        qapp.processEvents()
    assert seen_thread == [True]
    assert "chin" in win.title.text().lower()


def test_reduced_motion_snaps_guidance_dot(win, monkeypatch):
    monkeypatch.setenv("MG_REDUCED_MOTION", "1")
    win.apply_feedback(fb(target="right_mild", cue="right"))
    win.apply_feedback(fb(target="left_mild", cue="left"))
    assert win.ring._dot_angle == pytest.approx(180.0)
    assert not win.ring._timer.isActive()


def test_dot_glides_toward_new_target(win, qapp, monkeypatch):
    monkeypatch.delenv("MG_REDUCED_MOTION", raising=False)
    win.apply_feedback(fb(target="right_mild", cue="right"))
    win.apply_feedback(fb(target="up", cue="up"))
    before = win.ring._dot_angle
    for _ in range(60):
        win.ring._tick()
    after = win.ring._dot_angle
    assert before == pytest.approx(0.0)
    assert after == pytest.approx(90.0, abs=1.0)
    win.ring._timer.stop()
