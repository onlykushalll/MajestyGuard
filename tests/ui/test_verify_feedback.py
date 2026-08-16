"""UI feedback tests for deliberate soft-lock verification requests."""
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ui"))

from verification_feedback import request_verification_with_feedback  # noqa: E402


class _Writer:
    def __init__(self, accepted: bool):
        self.accepted = accepted
        self.sources: list[str] = []

    def verify_requested(self, source: str) -> bool:
        self.sources.append(source)
        return self.accepted


class _StateTarget:
    def __init__(self):
        self.states = []

    def apply_state(self, state) -> None:
        self.states.append(state)


def test_first_verify_request_immediately_shows_non_authoritative_verifying_state():
    writer = _Writer(accepted=True)
    shield = _StateTarget()
    island = _StateTarget()

    accepted = request_verification_with_feedback(writer, shield, island, "overlay_key")

    assert accepted is True
    assert writer.sources == ["overlay_key"]
    assert [state.name for state in shield.states] == ["verifying_lock"]
    assert [state.name for state in island.states] == ["verifying_lock"]


def test_rejected_repeat_request_does_not_restart_visual_verification():
    writer = _Writer(accepted=False)
    shield = _StateTarget()
    island = _StateTarget()

    accepted = request_verification_with_feedback(writer, shield, island, "overlay_key")

    assert accepted is False
    assert writer.sources == ["overlay_key"]
    assert shield.states == []
    assert island.states == []
