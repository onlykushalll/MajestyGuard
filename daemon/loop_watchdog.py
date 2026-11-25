"""Detects a wedged camera loop so locking never depends on the camera driver answering.

The daemon's main loop does everything in one thread: it drains lock commands, evaluates the
input-idle soft-lock and reads frames. ``cap.read()`` or ``cv2.VideoCapture(...)`` can block for a
long time (hung DirectShow/MSMF driver, USB unplug mid-read, resume from sleep). While that call is
stuck nothing else in the loop runs, so an idle PC would stay unlocked and the pill's emergency lock
would sit unread in its queue.

A second thread watches a heartbeat the loop refreshes on every iteration. When the heartbeat goes
stale it takes over exactly the two decisions that must not wait for the camera:

* the input-idle soft-lock (same rule the loop applies, so an active user is never locked), and
* an emergency lock requested from the UI.

Pure logic (stdlib only) so it is testable anywhere.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class LoopStallGuard:
    """Thread-safe heartbeat. ``beat()`` from the loop, ``stalled_for()`` from the watcher."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last_beat = clock()

    def beat(self) -> None:
        with self._lock:
            self._last_beat = self._clock()

    def stalled_for(self) -> float:
        with self._lock:
            return max(0.0, self._clock() - self._last_beat)

    def is_stalled(self, threshold_s: float) -> bool:
        return self.stalled_for() >= threshold_s


def should_idle_lock_while_stalled(
    *,
    stalled_for_s: float,
    threshold_s: float,
    state_can_idle_lock: bool,
    idle_seconds: float,
    idle_timeout_s: float,
) -> bool:
    """Would the loop have soft-locked for input idle if it were not stuck?

    Requires the stall AND the same idle condition the loop uses, so a present user (input
    recent) is never locked by a slow camera call.
    """
    if stalled_for_s < threshold_s or not state_can_idle_lock:
        return False
    return idle_seconds >= idle_timeout_s
