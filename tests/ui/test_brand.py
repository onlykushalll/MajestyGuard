import os
from pathlib import Path
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

from ui.brand import BrandTrace, brand_icon, paint_mark


def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_native_monogram_matches_supplied_svg_and_animates():
    svg = Path(__file__).resolve().parents[2] / "assets" / "majestyguard.svg"
    assert len(ET.parse(svg).getroot().findall(".//{*}path")) == 2

    def render(fn):
        canvas = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fn(p)
        p.end()
        return canvas

    original = render(lambda p: QSvgRenderer(str(svg)).render(p))
    native = render(lambda p: paint_mark(p, QRectF(0, 0, 200, 200), "#111418"))
    assert original == native
    animated = render(lambda p: paint_mark(p, QRectF(0, 0, 200, 200), "#111418", .43))
    assert animated != native


def test_reduced_motion_and_hidden_marks_stop_animations(monkeypatch):
    qapp = app()
    w = BrandTrace(size=100)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    w.set_busy(True)
    assert not w._timer.isActive()
    w.show()
    assert w._timer.isActive()
    w.setProperty("ccReduceMotion", True)
    w.set_busy(True)
    assert not w._timer.isActive()
    w.setProperty("ccReduceMotion", False)
    w.set_busy(True)
    assert w._timer.isActive()
    w.hide()
    assert not w._timer.isActive()
    assert brand_icon().pixmap(16, 16).size().width() == 16
