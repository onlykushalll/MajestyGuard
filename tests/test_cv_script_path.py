from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = [
    ROOT / "Install.ps1",
    ROOT / "build" / "staged" / "Install.ps1",
]
WORKER = ROOT / "src" / "MajestyGuard.Service" / "Worker.cs"
APP_CONFIG = ROOT / "src" / "MajestyGuard.Core" / "Models" / "AppConfig.cs"
UNINSTALLER = ROOT / "Uninstall.ps1"
BUILD = ROOT / "Build.ps1"


def test_cv_scripts_are_installed_to_programdata_with_explicit_acl():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")

        assert "$PROGRAMDATA_DIR" in text
        assert "$PROGRAMDATA_CV_DIR" in text
        assert '$PROGRAMDATA_DIR = Join-Path $env:ProgramData "MajestyGuard"' in text
        assert '$PROGRAMDATA_CV_DIR = Join-Path $PROGRAMDATA_DIR "CVEngine"' in text
        assert "Set-ExplicitProgramDataAcl" in text
        assert 'SYSTEM:(OI)(CI)F' in text
        assert 'Administrators:(OI)(CI)F' in text
        assert 'Users:(OI)(CI)R' in text
        assert "Clear-ProgramDataEfsEncryption" in text
        assert "[System.IO.FileAttributes]::Encrypted" in text
        assert "[System.IO.File]::Decrypt" in text
        assert 'Copy-Item "$source\\*" $INSTALL_DIR -Recurse -Force -Exclude "Install.ps1","Uninstall.ps1","CVEngine"' in text
        assert text.find("Clear-ProgramDataEfsEncryption") < text.rfind("Set-ExplicitProgramDataAcl")
        assert text.find("Copy-DirectoryContentsFiltered") < text.rfind("Set-ExplicitProgramDataAcl")


def test_worker_uses_configured_programdata_cv_script_path_with_read_probe():
    worker_text = WORKER.read_text(encoding="utf-8")
    config_text = APP_CONFIG.read_text(encoding="utf-8")

    assert "CvScriptPath" in config_text
    assert "CvScriptPath" in worker_text
    assert "File.OpenRead(scriptPath)" in worker_text
    assert "[CVEngine] FileStream read test: OK" in worker_text
    assert "[CVEngine] FileStream read test FAILED" in worker_text
    assert r"C:\Program Files\MajestyGuard\CVEngine\cv_server.py" not in worker_text


def test_uninstaller_removes_programdata_runtime_but_preserves_user_data_by_default():
    text = UNINSTALLER.read_text(encoding="utf-8")

    assert "$PROGRAMDATA_DIR = Join-Path $env:ProgramData \"MajestyGuard\"" in text
    assert "$PROGRAMDATA_DIR" in text
    assert "Remove MajestyGuard ProgramData runtime files" in text
    assert text.find("Remove MajestyGuard ProgramData runtime files") < text.find("Preserving user data")


def test_build_stage_clears_efs_encryption_from_cv_runtime():
    text = BUILD.read_text(encoding="utf-8")

    assert "Clear-EfsEncryption" in text
    assert "[System.IO.FileAttributes]::Encrypted" in text
    assert "[System.IO.File]::Decrypt" in text
    assert text.find("Copy-DirectoryContents") < text.rfind("Clear-EfsEncryption -Path $cvDestination")
    assert text.rfind("Clear-EfsEncryption -Path $cvDestination") < text.rfind("Test-StagePackage -Path $StageDir")
