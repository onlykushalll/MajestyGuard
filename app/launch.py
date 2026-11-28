"""First-paint startup and a short, readiness-driven handoff to the dashboard."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import sys

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QMessageBox, QVBoxLayout

from ui.brand import BrandTrace

_LOG = logging.getLogger(__name__)


class LaunchPanel(QFrame):
    first_paint = pyqtSignal()

    def __init__(self, settings_path):
        super().__init__(None, Qt.WindowType.SplashScreen)
        self._painted = False
        self.setWindowTitle("Opening MajestyGuard")
        self.setFixedSize(300, 230)
        self.setObjectName("launchPanel")
        self.setStyleSheet("""
            QFrame#launchPanel {background:#182026; border:1px solid #42515C;}
            QLabel {color:#EAF1F4; background:transparent; font:600 20px 'Segoe UI';}
            QLabel#launchStage {color:#B6C8D1; font:13px 'Segoe UI';}
        """)
        settings = QSettings(str(settings_path.with_name("appearance.ini")), QSettings.Format.IniFormat)
        self.setProperty("ccReduceMotion", settings.value("reduceMotion", False, type=bool))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark = BrandTrace(self, size=124)
        self.mark.set_busy(True)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(QLabel("MajestyGuard", self), 0, Qt.AlignmentFlag.AlignHCenter)
        self.stage = QLabel("Opening Control Center", self)
        self.stage.setObjectName("launchStage")
        layout.addWidget(self.stage, 0, Qt.AlignmentFlag.AlignHCenter)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(165)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.finished.connect(self._dismiss)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._painted:
            self._painted = True
            self.first_paint.emit()

    def finish(self):
        if self.property("ccReduceMotion") or os.environ.get("MG_UI_REDUCE_MOTION") == "1":
            self._dismiss()
        else:
            self._fade.start()

    def _dismiss(self):
        self.mark.set_busy(False)
        self.hide()


def _prepare_supervisor(root, settings_path, daemon_executable):
    # Non-Qt imports and path discovery only. All widgets belong to the GUI thread.
    from app import activity, diagnostics  # noqa: F401 - warm the dashboard's readers
    from app.runtime import Supervisor

    return Supervisor(root, sys.executable, settings_path, daemon_executable=daemon_executable)


def open_foreground(app, root, settings_path, find_daemon):
    panel = LaunchPanel(settings_path)
    screen = app.primaryScreen()
    if screen is not None:
        panel.move(screen.availableGeometry().center() - panel.rect().center())
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mg-launch")
    poll = QTimer(panel, interval=16)
    pending = None
    center = None

    def prepare():
        nonlocal pending
        if pending is None:
            pending = worker.submit(lambda: _prepare_supervisor(root, settings_path, find_daemon(root)))
            poll.start()

    def reveal():
        nonlocal center
        if pending is None or not pending.done():
            return
        poll.stop()
        try:
            from app.control_center import ControlCenter
            supervisor = pending.result()
            center = ControlCenter(supervisor, settings_path, async_actions=True)
            app.setWindowIcon(center.windowIcon())
            center.show_tray()
            center.show_center()
            if center.isVisible():
                center.repaint()
        except Exception:
            _LOG.exception("Could not open MajestyGuard")
            panel.hide()
            QMessageBox.critical(None, "MajestyGuard", "Could not open the Control Center.")
            app.exit(1)
        else:
            panel.finish()

    poll.timeout.connect(reveal)
    panel.first_paint.connect(prepare, Qt.ConnectionType.QueuedConnection)
    panel.show()
    # A Qt platform may defer or suppress painting (e.g. during display changes).
    fallback = QTimer(panel, interval=100, singleShot=True)
    fallback.timeout.connect(prepare)
    fallback.start()
    try:
        return app.exec()
    finally:
        poll.stop()
        worker.shutdown(wait=False, cancel_futures=True)
