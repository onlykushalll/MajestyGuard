"""Tests for user-specific named pipe SDDL security in pipe_security.py."""
import logging
import sys
from pathlib import Path

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
    # Should contain either current user's SID (S-1-5-) or fallback (IU)
    assert "S-1-5-" in sddl or "IU" in sddl


def test_build_user_pipe_sddl_logs_error_and_falls_back_to_iu_on_token_failure(monkeypatch, caplog):
    def mock_open_process_token(*args, **kwargs):
        raise RuntimeError("simulated token failure")

    monkeypatch.setattr(win32security, "OpenProcessToken", mock_open_process_token)

    with caplog.at_level(logging.ERROR):
        sddl = build_user_pipe_sddl()

    assert sddl == "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;IU)"
    assert any("Interactive-User (IU)" in record.message for record in caplog.records)
