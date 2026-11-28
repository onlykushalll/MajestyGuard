import os
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QMovie
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app import launch
from app.control_center import ControlCenter
from app.ui_smoke import PreviewSupervisor


def test_panel_animates_only_while_visible_and_dissolves(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    panel = launch.LaunchPanel(tmp_path / "settings.json")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.show()
    app.processEvents()
    assert panel.movie.isValid()
    assert panel.movie.frameCount() == 72
    assert panel.movie.state() == QMovie.MovieState.Running
    first = panel.movie.currentImage()
    assert not first.isNull()
    assert panel.movie.loopCount() == -1
    QTest.qWait(160)
    assert panel.movie.currentFrameNumber() > 0
    assert panel.movie.currentImage() != first
    panel.finish()
    assert panel.isVisible()
    panel._fade.setCurrentTime(panel._fade.duration())
    assert not panel.isVisible()
    assert panel.movie.state() == QMovie.MovieState.NotRunning


def test_reduced_motion_shows_original_still_without_playing(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    panel = launch.LaunchPanel(tmp_path / "settings.json")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.show()
    app.processEvents()
    try:
        assert panel.movie.state() == QMovie.MovieState.NotRunning
        assert not panel.still.isNull()
        assert not panel.mark.isVisible()
    finally:
        panel.finish()


def test_missing_animation_uses_native_fallback(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    monkeypatch.setattr(launch, "_LOADER_ART", tmp_path / "missing.webp")
    panel = launch.LaunchPanel(tmp_path / "settings.json")
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    panel.show()
    app.processEvents()
    try:
        assert panel.still.isNull()
        assert panel.mark.isVisible() and panel.mark._timer.isActive()
    finally:
        panel.hide()


def test_startup_paints_first_and_event_loop_stays_responsive_until_ready(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    original_quit_setting = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    seen = []
    enter = threading.Event()
    release = threading.Event()
    ready = threading.Event()
    panels = []
    centers = []

    class Panel(launch.LaunchPanel):
        def __init__(self, path):
            super().__init__(path)
            panels.append(self)
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            self.first_paint.connect(lambda: seen.append("paint"))

    def prepare(*_):
        seen.append("prepare")
        enter.set()
        if not release.wait(3):
            raise RuntimeError("Test failed to release preparation")
        return PreviewSupervisor()

    class Center(ControlCenter):
        def show_tray(self):
            centers.append(self)
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

        def show_center(self):
            seen.append("show")
            super().show_center()
            ready.set()

    def await_preparation():
        if enter.is_set():
            seen.append("gui-responsive")
            release.set()

    def await_handoff():
        if ready.is_set() and not panels[0].isVisible():
            app.exit(0)

    monkeypatch.setattr(launch, "LaunchPanel", Panel)
    monkeypatch.setattr(launch, "_prepare_supervisor", prepare)
    monkeypatch.setattr("app.control_center.ControlCenter", Center)
    monkeypatch.setattr(launch.QMessageBox, "critical", lambda *args: None)
    monitor = QTimer(interval=12)
    monitor.timeout.connect(await_preparation)
    monitor.timeout.connect(await_handoff)
    timeout = QTimer(interval=4000, singleShot=True)
    timeout.timeout.connect(lambda: app.exit(9))
    monitor.start()
    timeout.start()
    try:
        assert launch.open_foreground(app, tmp_path, tmp_path / "settings.json", lambda _: None) == 0
        assert seen[:3] == ["paint", "prepare", "gui-responsive"]
        assert seen.count("prepare") == 1 and seen.count("show") == 1
        assert not panels[0]._fade.state().value
        assert centers[0].status_text() == "Protection stopped"
    finally:
        release.set()
        timeout.stop()
        monitor.stop()
        for panel in panels:
            panel.hide()
        for center in centers:
            center._refresh_timer.stop()
            center.hide()
        app.setQuitOnLastWindowClosed(original_quit_setting)
