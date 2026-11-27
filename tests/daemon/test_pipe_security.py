"""Tests for user-specific named pipe SDDL security in pipe_security.py."""
import sys
from pathlib import Path

import pytest
import win32security

ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "daemon"
sys.path.insert(0, str(DAEMON))

from pipe_security import build_user_pipe_sddl


def test_build_user_pipe_sddl_format():
    sddl = build_user_pipe_sddl()
    assert "SY" in sddl
    assert "BA" in sddl
    assert "D:" in sddl
    assert "S-1-5-" in sddl
    assert ";;;IU)" not in sddl


def test_pipe_servers_refuse_to_start_when_user_sid_is_unavailable(monkeypatch):
    from cmd_server import CMDServer
    from ipc_server import IPCServer

    def mock_open_process_token(*args, **kwargs):
        raise RuntimeError("simulated token failure")

    monkeypatch.setattr(win32security, "OpenProcessToken", mock_open_process_token)

    for server in (CMDServer(lambda *_: None), IPCServer()):
        with pytest.raises(RuntimeError, match="current user SID"):
            server.start()
        assert server._thread is None
