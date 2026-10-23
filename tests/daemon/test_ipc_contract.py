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
