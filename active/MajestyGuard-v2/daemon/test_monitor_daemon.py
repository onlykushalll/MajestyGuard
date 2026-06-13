"""Tests for the lightweight monitor daemon (mg_monitor.py)."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
