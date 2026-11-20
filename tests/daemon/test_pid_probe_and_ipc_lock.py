"""Regression tests from the 2026-11 audit.

1. The monitor's PID liveness probe must never use os.kill(pid, 0): on Windows
   that call is TerminateProcess and would kill the daemon (or an unrelated
   process that reused the PID).
2. The UI pipe server must not hold its state lock while writing to the pipe:
   a stalled UI client would otherwise freeze the camera/state loop.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))


# ---- PID probe ---------------------------------------------------------------


def test_pid_probe_never_calls_os_kill(monkeypatch):
    import mg_monitor

    def boom(*a, **k):
        raise AssertionError("os.kill must not be used as a liveness probe")

    monkeypatch.setattr(mg_monitor.os, "kill", boom, raising=True)
    assert mg_monitor._is_pid_alive(os.getpid()) is True
    assert mg_monitor._is_pid_alive(0) is False
    assert mg_monitor._is_pid_alive(-5) is False


@pytest.mark.skipif(not hasattr(__import__("ctypes"), "windll"), reason="Windows-only semantics")
def test_pid_probe_does_not_terminate_a_live_process_and_sees_it_exit():
    import mg_monitor

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        for _ in range(3):                       # repeated probes must leave it running
            assert mg_monitor._is_pid_alive(child.pid) is True
        assert child.poll() is None
    finally:
        child.kill()
        child.wait(timeout=10)
    assert mg_monitor._is_pid_alive(child.pid) is False


def test_no_os_kill_signal_zero_probe_anywhere_in_daemon():
    for path in (ROOT / "daemon").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "mg_monitor.py":
            # allowed only in the POSIX branch, which is gated on "no ctypes.windll"
            idx = text.index("os.kill(pid, 0)")
            assert 'not hasattr(ctypes, "windll")' in text[max(0, idx - 200):idx]
            assert text.count("os.kill(pid, 0)") == 1
            continue
        assert "os.kill(pid, 0)" not in text and "os.kill(daemon_pid, 0)" not in text, path.name


# ---- IPC lock ------------------------------------------------------------------


def test_pipe_writes_happen_outside_the_state_lock():
    """Structural guard: no WriteFile call may sit under `with self._lock:`."""
    import ast

    src = (ROOT / "daemon" / "ipc_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and any(
            isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock"
            for i in node.items
        ):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("WriteFile", "FlushFileBuffers")):
                    offenders.append(inner.lineno)
    assert not offenders, f"pipe I/O while holding the state lock at lines {offenders}"


# ---- monitor loop resilience ---------------------------------------------------


def test_monitor_loop_survives_a_failing_tick(monkeypatch):
    import mg_monitor

    monkeypatch.setattr(mg_monitor.time, "sleep", lambda s: None)

    class Fake:
        _stop = False
        calls = 0

        def _tick(self):
            Fake.calls += 1
            if Fake.calls == 1:
                raise OSError("file in use")
            if Fake.calls >= 3:
                self._stop = True

    mg_monitor.MonitorDaemon._loop(Fake())
    assert Fake.calls == 3          # kept ticking after the exception
