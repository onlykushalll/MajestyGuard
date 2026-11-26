import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SERVICE_TESTS = (
    "tests/daemon/test_installer_safety.py",
    "tests/daemon/test_uninstall_safety.py",
    "tests/test_code_integrity_diagnostics.py",
    "tests/test_cv_script_path.py",
    "tests/test_python_resolution.py",
    "tests/test_service_only_overlay.py",
)


def test_legacy_service_artifact_tests_are_explicitly_opt_in():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "legacy_service" in config
    assert '-m "not legacy_service"' in config

    for relative_path in LEGACY_SERVICE_TESTS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "pytestmark = pytest.mark.legacy_service" in text


def test_daemon_modules_do_not_import_pyqt():
    """Assert daemon modules never import PyQt or PySide, preserving process isolation."""
    daemon_dir = ROOT / "daemon"
    for py_file in daemon_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("PyQt", "PySide")), (
                        f"Daemon file {py_file.name} directly imports Qt module {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith(("PyQt", "PySide")), (
                        f"Daemon file {py_file.name} imports from Qt module {node.module}"
                    )


def test_daemon_import_does_not_create_production_handlers_or_state(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import logging; before = logging.getLogger().handlers[:]; "
         "import main; assert before == logging.getLogger().handlers"],
        cwd=ROOT / "daemon",
        env={**os.environ, "LOCALAPPDATA": str(tmp_path), "MG_ENABLE_LOCK": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "MajestyGuard").exists()
