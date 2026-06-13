"""
idle_monitor.py - Keyboard/mouse inactivity timer.

Uses GetLastInputInfo() to measure desktop input inactivity. Overlay keypresses
are intentionally not treated as desktop activity; only successful owner
verification resets the monitor's fired state.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from typing import Callable, Mapping, Optional

log = logging.getLogger(__name__)

MIN_IDLE_TIMEOUT_S = 15.0
MAX_IDLE_TIMEOUT_S = 600.0
DEFAULT_IDLE_TIMEOUT_S = 90.0


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def get_idle_seconds() -> float:
    """Return seconds since the last local keyboard or mouse input."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    elapsed_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0.0, float(elapsed_ms) / 1000.0)


def clamp_idle_timeout(value: float) -> float:
    """Clamp idle timeout to the supported 30-600 second range."""
    return max(MIN_IDLE_TIMEOUT_S, min(MAX_IDLE_TIMEOUT_S, float(value)))


def read_idle_timeout(
    env: Optional[Mapping[str, str]] = None,
    *,
    warn: Optional[Callable[[str], None]] = None,
) -> float:
    """Read MG_IDLE_TIMEOUT, falling back to the legacy soft-lock env name."""
    source = os.environ if env is None else env
    raw = source.get("MG_IDLE_TIMEOUT") or source.get("MG_SOFT_LOCK_IDLE_SECONDS")
    emit = warn or (lambda message: log.warning(message))
    if raw is None or str(raw).strip() == "":
        return DEFAULT_IDLE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        emit(f"Invalid MG_IDLE_TIMEOUT={raw!r}; using {DEFAULT_IDLE_TIMEOUT_S:.0f}s")
        return DEFAULT_IDLE_TIMEOUT_S
    clamped = clamp_idle_timeout(value)
    if clamped != value:
        emit(f"MG_IDLE_TIMEOUT={value:.0f}s outside 30-600s; using {clamped:.0f}s")
    return clamped


class IdleMonitor:
    """Small polling monitor that emits IDLE_TIMEOUT once per idle stretch."""

    def __init__(
        self,
        *,
        idle_timeout_s: float,
        get_idle_seconds: Callable[[], float] = get_idle_seconds,
        emit: Callable[[str, float], None],
        poll_interval_s: float = 5.0,
    ):
        self.idle_timeout_s = clamp_idle_timeout(idle_timeout_s)
        self.get_idle_seconds = get_idle_seconds
        self.emit = emit
        self.poll_interval_s = max(0.1, float(poll_interval_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fired = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mg-idle-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def poll_once(self) -> None:
        idle_seconds = float(self.get_idle_seconds())
        if idle_seconds < self.idle_timeout_s:
            self._fired = False
            return
        if self._fired:
            return
        self._fired = True
        self.emit("IDLE_TIMEOUT", idle_seconds)

    def note_owner_verified(self) -> None:
        self._fired = False

    def note_overlay_input(self) -> None:
        """Overlay input is consumed by the lock surface and does not rearm idle."""
        return

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.poll_interval_s)
