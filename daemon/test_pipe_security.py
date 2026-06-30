"""Tests for user-specific named pipe SDDL security in pipe_security.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
