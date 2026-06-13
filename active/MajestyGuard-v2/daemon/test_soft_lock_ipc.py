from cmd_server import CMD_PIPE_NAME, cmd_payload, parse_cmd
from ipc_server import IPCServer


def test_ipc_server_accepts_soft_lock_states():
    server = IPCServer()

    server.broadcast_state("locked_passive", detail="idle_timeout")
    assert server.get_state()["state"] == "locked_passive"
    assert server.get_state()["detail"] == "idle_timeout"

    server.broadcast_state("soft_locked", detail="idle_timeout")
    assert server.get_state()["state"] == "soft_locked"
    assert server.get_state()["detail"] == "idle_timeout"

    server.broadcast_state("verifying_lock")
    assert server.get_state()["state"] == "verifying_lock"

    server.broadcast_state("social_lock")
    assert server.get_state()["state"] == "social_lock"

    server.broadcast_state("hostile_lock")
    assert server.get_state()["state"] == "hostile_lock"


def test_cmd_parser_accepts_verify_requested_and_emergency_lock_only():
    assert CMD_PIPE_NAME == r"\\.\pipe\MajestyGuard_CMD"
    assert parse_cmd('{"cmd":"verify_requested","source":"space"}\n') == ("verify_requested", "space")
    assert parse_cmd('{"cmd":"emergency_lock","source":"overlay"}\n') == ("emergency_lock", "overlay")
    assert parse_cmd('{"command":"verify_now","detail":"space"}\n') is None
    assert parse_cmd('{"cmd":"unlock_now"}\n') is None
    assert parse_cmd("not-json") is None


def test_cmd_payload_does_not_authorize_by_itself():
    payload = cmd_payload("verify_requested", "island")

    assert payload == {"cmd": "verify_requested", "source": "island"}
    assert "confidence" not in payload
    assert "liveness" not in payload
