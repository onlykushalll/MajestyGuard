"""Qt-free policy for acknowledging deliberate owner-verification requests."""
from __future__ import annotations

from typing import Protocol

from states import get_state


class _VerificationWriter(Protocol):
    def verify_requested(self, source: str) -> bool: ...


class _StateTarget(Protocol):
    def apply_state(self, state: object) -> None: ...


def request_verification_with_feedback(
    command_writer: _VerificationWriter,
    shield: _StateTarget | None,
    island: _StateTarget,
    source: str,
) -> bool:
    """Show a local, still-locked verifying state after one accepted command.

    The daemon remains the only authority that can clear the overlay.  This
    merely acknowledges the first deliberate request while the daemon finishes
    model startup or waits for the next camera frame.
    """
    if not command_writer.verify_requested(source):
        return False

    verifying = get_state("verifying_lock", detail="Starting secure check")
    if shield is not None:
        shield.apply_state(verifying)
    island.apply_state(verifying)
    return True
