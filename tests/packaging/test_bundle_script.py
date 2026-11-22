from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_bundle_script_builds_a_fresh_distribution_not_a_copied_venv():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "PyInstaller" in text
    assert ".venv" not in text
    assert "app\\main.py" in text


def test_build_requirements_pin_pyinstaller():
    text = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")

    assert any(line.startswith("pyinstaller==") for line in text.splitlines())


def test_bundle_script_requires_models_and_builds_all_three_processes():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "ModelDir" in text
    assert "InsightFaceModelDir" in text
    assert "MajestyGuardDaemon" in text
    assert "MajestyGuardUI" in text
    assert "${Description}:" in text
    assert "[string]$OutputDir," in text


def test_all_frozen_processes_use_onedir_bundles():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert text.count('"--onedir"') == 3
    assert '"--onefile"' not in text
    assert '"MajestyGuard"' in text
    assert '"MajestyGuardUI"' in text


def test_bundle_script_has_an_explicit_no_camera_smoke_test():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "--self-test" in text
    assert "MG_ENABLE_LOCK" in text
    assert '"0"' in text
    assert 'MajestyGuard\\MajestyGuard.exe' in text
