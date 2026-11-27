"""Render the real Control Center with isolated example readings, never a live supervisor."""
from __future__ import annotations

import argparse
import json
import io
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from app.control_center import ControlCenter
from app.runtime import RuntimeStatus
from daemon.audit_log import AuditLog


class Example:
    state = RuntimeStatus.RUNNING

    def status(self):
        return self.state

    def enrollment_present(self):
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--motion", action="store_true", help="also render a short GIF of the Qt animations")
    parser.add_argument("--brand-motion", action="store_true", help="capture the native enrollment loader in motion")
    parser.add_argument("--compare", type=Path, help="previous overview render at the same resolution")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["MG_UI_REDUCE_MOTION"] = "1"
    app = QApplication([])
    with TemporaryDirectory() as tmp:
        state = Path(tmp)
        now = time.time()
        audit = AuditLog(state / "audit.log")
        for seconds, event, info in ((1800, "daemon_start", {}), (420, "soft_lock", {"reason": "idle"}),
                                    (250, "verify_requested", {}), (248, "unlock_face", {})):
            with patch("daemon.audit_log.time.time", return_value=now-seconds):
                audit.record(event, **info)
        (state / "camera_health.json").write_text(json.dumps({
            "status": "healthy", "capture_open": False, "updated_at": now, "last_backend": "dshow"}))
        (state / "lock_state.txt").write_text("active")
        (state / "daemon.log").write_text("Cold start complete: total=1270ms\n")
        view = ControlCenter(Example(), state / "settings.json")
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        view._quick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        view._refresh_timer.stop()
        view.resize(1000, 700)
        view.show()
        app.processEvents()

        def capture(widget, name):
            while view._history_pending is not None:
                view._history_pending[1].result(timeout=5)
                view._refresh_history(state / "audit.log")
            app.processEvents()
            path = args.output / name
            if not widget.grab().save(str(path)):
                raise OSError(f"Could not render {path}")

        capture(view, "overview.png")
        if args.compare:
            old = QPixmap(str(args.compare))
            new = view.grab()
            if old.isNull() or old.size() != new.size():
                raise ValueError("Comparison requires two equal-sized window renders")
            board = QPixmap(2*new.width()+24, new.height()+52)
            board.fill(QColor("#E4E7EA"))
            painter = QPainter(board)
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
            for x, title, pix in ((8, "Before", old), (16+new.width(), "Now", new)):
                painter.drawText(x+4, 30, title)
                painter.drawPixmap(x, 44, pix)
            painter.end()
            if not board.save(str(args.output / "before-after.png")):
                raise OSError("Could not save comparison")
        view.show_page(2)
        app.processEvents()
        capture(view, "activity.png")
        view._quick.adjustSize()
        view._quick.show()
        app.processEvents()
        capture(view._quick, "tray.png")
        view._quick.hide()
        if args.brand_motion:
            from PIL import Image
            from PyQt6.QtTest import QTest
            from ui.coverage_enrollment_window import CoverageEnrollmentWindow

            os.environ.pop("MG_UI_REDUCE_MOTION", None)
            enrollment = CoverageEnrollmentWindow()
            enrollment.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            enrollment.setProperty("ccReduceMotion", False)
            enrollment.set_loading(True)
            enrollment.title.setText("Preparing face setup")
            enrollment.hint.setText("Loading the recognition engine on this PC.")
            enrollment.resize(420, 550)
            enrollment.show()
            frames = []
            for _ in range(30):
                QTest.qWait(48)
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                enrollment.grab().save(buffer, "PNG")
                with Image.open(io.BytesIO(bytes(data))) as bitmap:
                    frames.append(bitmap.convert("RGB"))
            frames[0].save(args.output / "enrollment-loader.gif", save_all=True,
                           append_images=frames[1:], duration=50, loop=0)
            enrollment.set_loading(False)
            enrollment.hide()
            os.environ["MG_UI_REDUCE_MOTION"] = "1"
        if args.motion:
            from PIL import Image

            os.environ.pop("MG_UI_REDUCE_MOTION")
            view.show_page(0)
            view._tab_bar.select(view._nav[0], False)
            def camera(opened):
                (state / "camera_health.json").write_text(json.dumps({
                    "status": "healthy", "capture_open": opened, "updated_at": time.time()}))
                view.refresh()

            camera(False)
            view.refresh()
            frames, delays = [], []

            def frame(delay):
                image = view.grab().scaledToWidth(800, Qt.TransformationMode.SmoothTransformation)
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                image.save(buffer, "PNG")
                with Image.open(io.BytesIO(bytes(data))) as bitmap:
                    frames.append(bitmap.convert("RGB"))
                delays.append(delay)

            frame(600)
            for opened in (True, False):
                camera(opened)
                for ms in range(0, 421, 28):
                    view._aperture._travel.setCurrentTime(ms)
                    frame(28 if ms < 420 else 800)
            for status in (RuntimeStatus.PAUSED, RuntimeStatus.RUNNING):
                view._supervisor.state = status
                view.refresh()
                for ms in (0, 37, 74, 111, 148, 185):
                    view._primary_button._tween.setCurrentTime(ms)
                    frame(37 if ms < 185 else 650)
            view.show_page(2)
            for ms in (0, 33, 66, 99, 132, 165, 205):
                view._tab_bar._motion.setCurrentTime(ms)
                frame(33 if ms < 205 else 1000)
            frames[0].save(args.output / "desk-motion.gif", save_all=True, append_images=frames[1:],
                           duration=delays, loop=0)
        view.hide()


if __name__ == "__main__":
    main()
