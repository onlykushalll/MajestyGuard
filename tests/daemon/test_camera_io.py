from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from camera_io import read_bgr_frame, release_capture


@pytest.mark.parametrize("value", [None, np.empty((0, 2, 3), np.uint8),
                                   np.zeros((2, 2), np.uint8), np.zeros((2, 2, 3), np.float32)])
def test_invalid_frame_never_reaches_recognition(value):
    assert read_bgr_frame(SimpleNamespace(read=lambda: (True, value))) is None


def test_frame_is_not_copied():
    frame = np.zeros((2, 2, 3), np.uint8)
    assert read_bgr_frame(SimpleNamespace(read=lambda: (True, frame))) is frame


@pytest.mark.parametrize("error", [OSError, cv2.error])
def test_disconnected_backend_uses_existing_failure_path(error):
    def disconnected():
        raise error("device unavailable")
    cap = SimpleNamespace(read=disconnected, release=disconnected)
    assert read_bgr_frame(cap) is None
    release_capture(cap)
