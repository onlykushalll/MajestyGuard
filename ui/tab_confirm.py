"""Two-step confirmation for the Tab -> Windows password fallback.

While the camera works, Tab goes straight to the Windows lock screen. When the
camera is faulty the overlay is the only thing standing between the desktop and
whoever is at the keyboard, so handing over to Windows needs a deliberate second
press: Tab once to arm, Tab again within ``window_s`` seconds to confirm. A stray
or accidental press just expires. Pure logic, no Qt, so it is trivially testable.
"""
from __future__ import annotations

import time
from typing import Callable

ACTION_LOCK = "lock"
ACTION_PROMPT = "prompt"


class TabConfirmGate:
    def __init__(self, window_s: float = 6.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._window_s = max(0.5, float(window_s))
        self._clock = clock
        self._armed_until = 0.0

    @property
    def window_s(self) -> float:
        return self._window_s

    @property
    def armed(self) -> bool:
        return self._clock() < self._armed_until

    def press(self, confirmation_required: bool) -> str:
        """Return ACTION_LOCK to hand over to Windows, ACTION_PROMPT to ask again."""
        if not confirmation_required:
            self._armed_until = 0.0
            return ACTION_LOCK
        if self.armed:
            self._armed_until = 0.0
            return ACTION_LOCK
        self._armed_until = self._clock() + self._window_s
        return ACTION_PROMPT

    def reset(self) -> None:
        self._armed_until = 0.0
