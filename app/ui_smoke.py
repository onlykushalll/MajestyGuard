"""Render the packaged Control Center without touching the desktop or protection."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app.runtime import RuntimeStatus


class PreviewSupervisor:
    def status(self):
        return RuntimeStatus.STOPPED

    def enrollment_present(self):
        return True


def run() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MG_UI_REDUCE_MOTION"] = "1"
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from app.control_center import ControlCenter

    app = QApplication([])
    with TemporaryDirectory(prefix="mg-ui-smoke-") as data:
        center = ControlCenter(PreviewSupervisor(), Path(data) / "settings.json")
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
        center._quick.adjustSize()
        center._quick.show()
        app.processEvents()
        if not _has_content(center._quick):
            return 3
        center._quick.routes["activity"].click()
        app.processEvents()
        if center.current_page() != 2 or center._quick.isVisible():
            return 4
        center.hide()
        return 0


def _has_content(widget) -> bool:
    frame = widget.grab().toImage()
    if frame.isNull() or min(frame.width(), frame.height()) < 16:
        return False
    return len({frame.pixel(x, y) for y in range(0, frame.height(), 3)
                for x in range(0, frame.width(), 3)}) > 8
