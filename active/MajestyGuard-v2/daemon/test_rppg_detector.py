import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import rppg_detector
from rppg_detector import CHROMrPPGDetector


def test_rppg_detector_does_not_use_bare_except():
    tree = ast.parse(Path(rppg_detector.__file__).read_text(encoding="utf-8"))

    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]

    assert handlers == []


def test_skin_roi_returns_none_for_invalid_face_box():
    detector = CHROMrPPGDetector()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=None)

    assert detector._skin_roi(frame, face) is None


def test_chrom_returns_neutral_when_filter_fails(monkeypatch):
    detector = CHROMrPPGDetector()
    detector._rgb_buf.extend([(60.0 + i, 55.0 + i, 50.0 + i) for i in range(detector.MIN_FRAMES)])

    def fail_filter(*_args, **_kwargs):
        raise ValueError("bad filter")

    monkeypatch.setattr(rppg_detector.scipy_signal, "butter", fail_filter)

    assert detector._chrom() == 0.5
