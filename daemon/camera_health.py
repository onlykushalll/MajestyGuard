"""Camera health tracking, diagnosis and self-healing schedule.

Pure logic with no OpenCV, Qt or Windows dependency at import time, so the
daemon can reason about a failing camera (and unit tests can exercise every
branch) without a device. The daemon feeds it events -- open success/failure,
read failure, per-frame statistics -- and asks it three questions:

* ``ready_to_retry()``  -- is it time for another heal attempt (exponential
  backoff, so a dead camera never causes a busy loop)?
* ``backends()``        -- which capture backends should the next open try?
* ``diagnosis()``       -- what is wrong, in words a person can act on?

A persistent snapshot (``snapshot()`` / ``write_snapshot()``) lets the Control
Center and diagnostics show the same picture without touching the camera.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional


class CameraStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"      # a fault was seen; retrying quickly
    RECOVERING = "recovering"  # several failures; retrying with backoff
    FAILED = "failed"          # persistent; still retrying at the slowest rate


class CameraFault(Enum):
    NONE = "none"
    OPEN_FAILED = "open_failed"          # device cannot be opened (busy / missing)
    PRIVACY_BLOCKED = "privacy_blocked"  # Windows camera privacy switch is off
    NO_FRAMES = "no_frames"              # opens but returns no frames
    BLACK_FRAMES = "black_frames"        # lens covered / shutter closed
    FROZEN_FRAMES = "frozen_frames"      # identical buffers: hung driver


@dataclass(frozen=True)
class Diagnosis:
    fault: CameraFault
    headline: str
    hint: str

    def overlay_text(self) -> str:
        return f"{self.headline}. {self.hint}" if self.hint else self.headline


_DIAGNOSES: Dict[CameraFault, Diagnosis] = {
    CameraFault.NONE: Diagnosis(CameraFault.NONE, "Camera OK", ""),
    CameraFault.OPEN_FAILED: Diagnosis(
        CameraFault.OPEN_FAILED,
        "Camera unavailable",
        "Close other apps using the camera. Retrying automatically",
    ),
    CameraFault.PRIVACY_BLOCKED: Diagnosis(
        CameraFault.PRIVACY_BLOCKED,
        "Camera blocked by Windows",
        "Turn on Settings > Privacy & security > Camera. Retrying automatically",
    ),
    CameraFault.NO_FRAMES: Diagnosis(
        CameraFault.NO_FRAMES,
        "Camera not responding",
        "Replug it or toggle it off and on. Retrying automatically",
    ),
    CameraFault.BLACK_FRAMES: Diagnosis(
        CameraFault.BLACK_FRAMES,
        "Camera view is dark",
        "Uncover the lens or add light",
    ),
    CameraFault.FROZEN_FRAMES: Diagnosis(
        CameraFault.FROZEN_FRAMES,
        "Camera froze",
        "Reconnecting it automatically",
    ),
}

# Capture backends in preference order. Names are mapped to OpenCV constants by
# the caller so this module stays free of cv2.
BACKEND_DSHOW = "dshow"
BACKEND_ANY = "any"
BACKEND_MSMF = "msmf"


def windows_camera_privacy_blocked() -> Optional[bool]:
    """True when Windows' camera privacy toggle denies access; None if unknown."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    subkey = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    answered = False
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Value")
        except OSError:
            continue
        answered = True
        if str(value).strip().lower() == "deny":
            return True
    return False if answered else None


def frame_signature(frame) -> Optional[int]:
    """Cheap fingerprint of a frame for frozen-buffer detection."""
    try:
        return hash(frame[::40, ::40].tobytes())
    except Exception:
        return None


def frame_brightness(frame) -> Optional[float]:
    try:
        return float(frame[::16, ::16].mean())
    except Exception:
        return None


class CameraHealth:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
        black_mean_threshold: float = 3.0,
        black_frames_limit: int = 45,
        frozen_frames_limit: int = 60,
        privacy_probe: Optional[Callable[[], Optional[bool]]] = None,
        history_size: int = 40,
    ) -> None:
        self._clock = clock
        self._base_delay_s = max(0.05, float(base_delay_s))
        self._max_delay_s = max(self._base_delay_s, float(max_delay_s))
        self._black_mean_threshold = float(black_mean_threshold)
        self._black_frames_limit = max(1, int(black_frames_limit))
        self._frozen_frames_limit = max(2, int(frozen_frames_limit))
        self._privacy_probe = privacy_probe
        self._events: Deque[str] = deque(maxlen=max(5, int(history_size)))
        self.fault = CameraFault.NONE
        self.consecutive_open_failures = 0
        self.consecutive_read_failures = 0
        self.total_recoveries = 0
        self.last_backend: Optional[str] = None
        self._next_retry_at = 0.0
        self._black_run = 0
        self._frozen_run = 0
        self._last_signature: Optional[int] = None

    # ------------------------------------------------------------ events
    def record_open_success(self, backend: Optional[str] = None) -> None:
        if self.fault is not CameraFault.NONE:
            self.total_recoveries += 1
            self._note(f"recovered after {self.fault.value} (backend={backend})")
        self.fault = CameraFault.NONE
        self.consecutive_open_failures = 0
        self.consecutive_read_failures = 0
        self._black_run = 0
        self._frozen_run = 0
        self._last_signature = None
        self._next_retry_at = 0.0
        if backend:
            self.last_backend = backend

    def record_open_failure(self, detail: str = "") -> CameraFault:
        self.consecutive_open_failures += 1
        blocked = self._privacy_blocked()
        self.fault = CameraFault.PRIVACY_BLOCKED if blocked else CameraFault.OPEN_FAILED
        self._schedule_retry()
        self._note(f"open failed x{self.consecutive_open_failures} ({self.fault.value}) {detail}".strip())
        return self.fault

    def record_read_success(self) -> None:
        self.consecutive_read_failures = 0

    def record_read_failure(self) -> int:
        self.consecutive_read_failures += 1
        return self.consecutive_read_failures

    def record_no_frames(self) -> CameraFault:
        """The camera opened but stopped producing frames (threshold reached)."""
        self.consecutive_read_failures = 0
        self.fault = CameraFault.NO_FRAMES
        self._schedule_retry()
        self._note("no frames from an open camera")
        return self.fault

    def observe_frame(self, brightness: Optional[float], signature: Optional[int]) -> CameraFault:
        """Feed per-frame statistics; returns BLACK/FROZEN when sustained."""
        if brightness is not None and brightness < self._black_mean_threshold:
            self._black_run += 1
        else:
            self._black_run = 0
        if signature is not None and signature == self._last_signature:
            self._frozen_run += 1
        else:
            self._frozen_run = 0
        self._last_signature = signature

        if self._frozen_run >= self._frozen_frames_limit:
            if self.fault is not CameraFault.FROZEN_FRAMES:
                self._note("identical frames: driver looks hung")
            self.fault = CameraFault.FROZEN_FRAMES
            self._frozen_run = 0
            self._schedule_retry()
            return self.fault
        if self._black_run >= self._black_frames_limit:
            if self.fault is not CameraFault.BLACK_FRAMES:
                self._note("sustained black frames: lens covered or shutter closed")
            self.fault = CameraFault.BLACK_FRAMES
            return self.fault
        if self.fault is CameraFault.BLACK_FRAMES and self._black_run == 0:
            self.fault = CameraFault.NONE
            self._note("image returned")
        return CameraFault.NONE if self.fault is CameraFault.NONE else self.fault

    # ---------------------------------------------------------- schedule
    def ready_to_retry(self) -> bool:
        return self._clock() >= self._next_retry_at

    def request_retry_now(self) -> None:
        self._next_retry_at = 0.0

    def seconds_until_retry(self) -> float:
        return max(0.0, self._next_retry_at - self._clock())

    def backends(self) -> List[str]:
        """Backend order for the next open. MSMF joins after repeated failures."""
        order = [BACKEND_DSHOW, BACKEND_ANY]
        if self.consecutive_open_failures >= 2:
            order.append(BACKEND_MSMF)
        if self.consecutive_open_failures >= 4 and self.last_backend in order:
            # A backend that worked before is tried first once the usual order
            # has failed several times in a row.
            order.remove(self.last_backend)
            order.insert(0, self.last_backend)
        return order

    # ---------------------------------------------------------- reporting
    @property
    def status(self) -> CameraStatus:
        if self.fault is CameraFault.NONE:
            return CameraStatus.HEALTHY
        if self.consecutive_open_failures >= 8:
            return CameraStatus.FAILED
        if self.consecutive_open_failures >= 3:
            return CameraStatus.RECOVERING
        return CameraStatus.DEGRADED

    def diagnosis(self) -> Diagnosis:
        return _DIAGNOSES[self.fault]

    def snapshot(self) -> Dict[str, object]:
        diag = self.diagnosis()
        return {
            "status": self.status.value,
            "fault": self.fault.value,
            "headline": diag.headline,
            "hint": diag.hint,
            "open_failures": self.consecutive_open_failures,
            "recoveries": self.total_recoveries,
            "next_retry_in_s": round(self.seconds_until_retry(), 1),
            "backends": self.backends(),
            "last_backend": self.last_backend,
            "updated_at": round(time.time(), 1),
            "events": list(self._events)[-10:],
        }

    def write_snapshot(self, path: Path) -> bool:
        """Atomically persist the snapshot; never raises."""
        try:
            path = Path(path)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.snapshot(), separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    # ----------------------------------------------------------- internals
    def _schedule_retry(self) -> None:
        failures = max(1, self.consecutive_open_failures)
        delay = min(self._max_delay_s, self._base_delay_s * (2 ** (failures - 1)))
        self._next_retry_at = self._clock() + delay

    def _privacy_blocked(self) -> bool:
        if self._privacy_probe is None:
            return False
        try:
            return bool(self._privacy_probe())
        except Exception:
            return False

    def _note(self, message: str) -> None:
        self._events.append(message)
