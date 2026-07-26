"""Tests for the lightweight monitor daemon (mg_monitor.py)."""
import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "daemon"

BANNED_MODULES = {
    "numpy", "cv2", "insightface", "mediapipe", "onnxruntime",
    "PIL", "torch", "tensorflow", "scipy", "sklearn", "pandas",
    "win32file", "win32pipe", "win32security", "pywintypes", "win32api",
    "PyQt6", "PyQt5",
}


def test_mg_monitor_imports_nothing_outside_stdlib():
    """mg_monitor.py must only import stdlib + ctypes."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    violations = imported & BANNED_MODULES
    assert not violations, f"mg_monitor.py imports banned modules: {violations}"


def test_mg_monitor_reads_idle_timeout_from_env():
    """mg_monitor.py must support MG_IDLE_TIMEOUT env var."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "MG_IDLE_TIMEOUT" in source


def test_mg_monitor_writes_monitor_pid():
    """mg_monitor.py must write monitor.pid on startup."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "monitor.pid" in source


def test_mg_monitor_reads_daemon_pid_for_watchdog():
    """mg_monitor.py must read daemon.pid for watchdog checks."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "daemon.pid" in source


def test_mg_monitor_reads_lock_state():
    """mg_monitor.py must read lock_state.txt for watchdog decisions."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "lock_state.txt" in source


def test_mg_monitor_has_main_entry_point():
    """mg_monitor.py must have if __name__ == '__main__' block."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "__name__" in source
    assert "__main__" in source


def test_mg_monitor_uses_getlastinputinfo():
    """mg_monitor.py must use GetLastInputInfo for idle detection."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "GetLastInputInfo" in source


def test_mg_monitor_launches_main_py():
    """mg_monitor.py must launch main.py (not itself) as the full daemon."""
    source = (DAEMON / "mg_monitor.py").read_text(encoding="utf-8")
    assert "main.py" in source


def _load_monitor_module():
    path = DAEMON / "mg_monitor.py"
    spec = importlib.util.spec_from_file_location("mg_monitor_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monitor_launches_bounded_idle_check_not_force_lock(monkeypatch):
    monitor = _load_monitor_module()
    captured = {}

    class FakeProcess:
        pid = 123

    monkeypatch.setattr(monitor.subprocess, "Popen", lambda _args, **kwargs: captured.update(kwargs) or FakeProcess())
    monkeypatch.setattr(monitor, "_MG_STATE_DIR", Path("C:/tmp/mg-monitor-test"))

    monitor._launch_full_daemon()

    assert captured["env"]["MG_IDLE_CHECK_MODE"] == "1"
    assert "MG_FORCE_LOCK_STARTUP" not in captured["env"]


def test_monitor_keeps_same_idle_stretch_latched_after_owner_success(monkeypatch, tmp_path):
    monitor = _load_monitor_module()
    monkeypatch.setattr(monitor, "_MG_STATE_DIR", tmp_path)
    monkeypatch.setattr(monitor, "IDLE_CHECK_RESULT_FILE", tmp_path / "idle_check_result.txt")
    (tmp_path / "idle_check_result.txt").write_text("OWNER_VERIFIED\n", encoding="utf-8")
    watcher = monitor.MonitorDaemon()
    watcher._daemon_proc = type("Exited", (), {"poll": lambda self: 0})()
    watcher._idle_fired = True

    assert watcher._is_daemon_running() is False
    assert watcher._idle_fired is True


def test_tick_terminates_prewarmed_daemon_if_user_active_before_full_idle(monkeypatch, tmp_path):
    """If the user goes active again before reaching the full idle
    timeout, a pre-warmed daemon that's still PENDING (hasn't opened the
    camera or shown UI yet) must be terminated rather than left alive in
    memory until the next uninterrupted idle stretch."""
    monitor = _load_monitor_module()
    monkeypatch.setattr(monitor, "_MG_STATE_DIR", tmp_path)
    result_file = tmp_path / "idle_check_result.txt"
    result_file.write_text("PENDING", encoding="utf-8")
    monkeypatch.setattr(monitor, "IDLE_CHECK_RESULT_FILE", result_file)
    # User is active now (well below any reasonable prewarm threshold).
    monkeypatch.setattr(monitor, "get_idle_seconds", lambda: 1.0)

    terminated = {"called": False}

    class FakeAliveProcess:
        pid = 999

        def poll(self):
            return None  # still running

        def terminate(self):
            terminated["called"] = True

    watcher = monitor.MonitorDaemon()
    watcher._idle_timeout = 60.0
    watcher._idle_fired = True  # a pre-warm launch already fired earlier
    watcher._daemon_proc = FakeAliveProcess()

    watcher._tick()

    assert terminated["called"] is True
    assert watcher._idle_fired is False


def test_tick_does_not_terminate_daemon_once_it_progressed_past_pending(monkeypatch, tmp_path):
    """Once the pre-warmed daemon has moved past PENDING (e.g. it reached
    the probe and wrote a real result), it must NOT be killed just
    because the user's idle counter dipped -- it's already doing
    something real, not just waiting."""
    monitor = _load_monitor_module()
    monkeypatch.setattr(monitor, "_MG_STATE_DIR", tmp_path)
    result_file = tmp_path / "idle_check_result.txt"
    result_file.write_text("OWNER_VERIFIED", encoding="utf-8")
    monkeypatch.setattr(monitor, "IDLE_CHECK_RESULT_FILE", result_file)
    monkeypatch.setattr(monitor, "get_idle_seconds", lambda: 1.0)

    terminated = {"called": False}

    class FakeAliveProcess:
        pid = 999

        def poll(self):
            return None

        def terminate(self):
            terminated["called"] = True

    watcher = monitor.MonitorDaemon()
    watcher._idle_timeout = 60.0
    watcher._idle_fired = True
    watcher._daemon_proc = FakeAliveProcess()

    watcher._tick()

    assert terminated["called"] is False
