"""Boundary between OpenCV capture backends and the BGR recognition pipeline."""
import cv2
import numpy as np


def read_bgr_frame(cap):
    try:
        ok, frame = cap.read()
    except (cv2.error, OSError):
        return None
    if (not ok or not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0):
        return None
    return frame


def release_capture(cap):
    if cap is not None:
        try:
            cap.release()
        except (cv2.error, OSError):
            # A disconnected backend may already have destroyed the native handle.
            pass
