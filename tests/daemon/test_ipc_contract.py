"""Tests for IPC server contract and state validation."""
from ipc_server import IPCServer


def test_ipc_server_accepts_verify_failed_state():
    """verify_failed must be in VALID_STATES so SoftLock failure/cooldown
    feedback is not silently dropped by IPCServer.broadcast_state."""
    server = IPCServer()
    server.broadcast_state("verify_failed", detail="Retry cooldown (4s)")
    state = server.get_state()
    assert state["state"] == "verify_failed"
    assert state["detail"] == "Retry cooldown (4s)"


def test_ipc_server_accepts_camera_fault_state():
    """camera_fault drives the overlay's Tab-twice Windows fallback."""
    server = IPCServer()
    server.broadcast_state("camera_fault", detail="Camera unavailable. Retrying automatically")
    state = server.get_state()
    assert state["state"] == "camera_fault"
    assert "Retrying" in state["detail"]


def test_every_state_the_daemon_broadcasts_is_accepted_and_drawable():
    """A state the daemon sends but the pipe drops (or the island cannot draw) is a silent UI bug:
    'idle_detected' was broadcast for weeks while IPCServer ignored it."""
    import re
    import sys
    from pathlib import Path

    from ipc_server import VALID_STATES

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "ui"))
    from states import STATES

    broadcast = set()
    for source in (root / "daemon").glob("*.py"):
        broadcast.update(re.findall(r'broadcast_state\(\s*"([a-z_]+)"', source.read_text(encoding="utf-8")))
    assert broadcast, "no broadcast_state literals found - the scan is broken"

    assert broadcast <= VALID_STATES, sorted(broadcast - VALID_STATES)
    # every state the pipe accepts must be something the island can draw (exit is a control message)
    assert (VALID_STATES - {"exit"}) <= set(STATES), sorted(VALID_STATES - {"exit"} - set(STATES))


def test_ipc_server_accepts_idle_detected_state():
    server = IPCServer()
    server.broadcast_state("idle_detected", detail="System Idle Detected")
    assert server.get_state()["state"] == "idle_detected"
