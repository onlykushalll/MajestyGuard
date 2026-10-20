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
