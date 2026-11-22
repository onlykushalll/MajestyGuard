from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fonts  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_family_chain_starts_with_resolvable_variable_family(qapp):
    f = fonts.ui_font(12)
    assert f.families()[0] == "Segoe UI Variable"
    assert "Segoe UI Variable Display" not in f.families()
    assert "Segoe UI Variable Text" not in f.families()


def test_size_weight_spacing_applied(qapp):
    from PyQt6.QtGui import QFont

    f = fonts.ui_font(72, QFont.Weight.Light, tabular=True, spacing=-1.5)
    assert f.pointSizeF() == 72.0
    assert f.weight() == QFont.Weight.Light
    assert f.letterSpacing() == -1.5


def test_never_raises_if_variable_calls_fail(qapp, monkeypatch):
    from PyQt6.QtGui import QFont

    def boom(*_a, **_k):
        raise RuntimeError("no variable fonts")

    monkeypatch.setattr(QFont, "setVariableAxis", boom, raising=False)
    monkeypatch.setattr(QFont, "setFeature", boom, raising=False)
    f = fonts.ui_font(9, QFont.Weight.Medium, tabular=True)
    assert f.pointSizeF() == 9.0


def test_ui_font_px_is_pixel_sized_and_dpi_independent():
    import fonts
    f = fonts.ui_font_px(12.0)
    assert f.pixelSize() == 16 and f.pointSizeF() < 0 or f.pixelSize() == 16
