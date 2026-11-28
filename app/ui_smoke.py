"""Render the packaged Control Center without touching the desktop or protection."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runtime import RuntimeStatus


class PreviewSupervisor:
    runtime_status = RuntimeStatus.STOPPED

    def status(self):
        return self.runtime_status

    def enrollment_present(self):
        return True


def run() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MG_UI_REDUCE_MOTION"] = "1"
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QMovie
    from PyQt6.QtWidgets import QApplication

    from app.control_center import ControlCenter
    from app.launch import LaunchPanel
    from app.aperture import _INTRO_PATH

    app = QApplication([])
    with TemporaryDirectory(prefix="mg-ui-smoke-") as data:
        settings_path = Path(data) / "settings.json"
        panel = LaunchPanel(settings_path)
        panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        panel.show()
        app.processEvents()
        if not _has_content(panel) or not _has_content(panel.mark):
            return 5
        intro = QMovie(str(_INTRO_PATH))
        if not intro.isValid() or intro.frameCount() != 204:
            return 9
        panel.finish()
        if panel.isVisible():
            return 6
        supervisor = PreviewSupervisor()
        center = ControlCenter(supervisor, settings_path)
        center.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        center._quick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        center._refresh_timer.stop()
        center.resize(960, 680)
        center.show()
        app.processEvents()
        if center.current_page() != 0 or center.primary_action_text() != "Start protection":
            return 1
        if not _has_content(center) or not _has_content(center._brand):
            return 2
        supervisor.runtime_status = RuntimeStatus.RUNNING
        center.refresh()
        aperture = center._aperture
        if (aperture._art_opacity != 1 or aperture._movie is None
                or aperture._movie.frameCount() < 2 or not _has_content(aperture)):
            return 8
        center.show_page(5)
        app.processEvents()
        if (center._health_values["Camera"].text() != "Not reported"
                or center._startup_breakdown.text() != "--"
                or not _has_content(center._stack.currentWidget())):
            return 10
        center.show_page(0)
        supervisor.runtime_status = RuntimeStatus.STOPPED
        center.refresh()
        center._quick.adjustSize()
        center._quick.show()
        app.processEvents()
        if not _has_content(center._quick):
            return 3
        center._quick.routes["activity"].click()
        app.processEvents()
        if center.current_page() != 2 or center._quick.isVisible():
            return 4
        center.show_about()
        app.processEvents()
        dialog = center._about_dialog
        if (dialog is None or dialog.property("ccTheme") != center.property("ccTheme")
                or not _has_content(dialog)):
            return 7
        dialog.accept()
        center.hide()
        return 0


def _has_content(widget) -> bool:
    frame = widget.grab().toImage()
    if frame.isNull() or min(frame.width(), frame.height()) < 16:
        return False
    return len({frame.pixel(x, y) for y in range(0, frame.height(), 3)
                for x in range(0, frame.width(), 3)}) > 8
