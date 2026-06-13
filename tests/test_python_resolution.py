from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = [
    ROOT / "Install.ps1",
    ROOT / "build" / "staged" / "Install.ps1",
]
WORKER = ROOT / "src" / "MajestyGuard.Service" / "Worker.cs"


def test_installer_prefers_known_good_311_venv_and_never_system_python():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        source_venv = r'C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\python.exe'

        assert source_venv in text
        assert "$pythonCommand = Get-Command python" not in text
        assert "Python\\bin\\python.exe" not in text
        assert "Programs\\Python" not in text
        assert "Resolve-CvPythonPath" in text
        assert "3.11" in text


def test_worker_fails_before_launch_when_python_is_not_311():
    text = WORKER.read_text(encoding="utf-8")

    assert "ValidateCvPythonVersion" in text
    assert "--version" in text
    assert "FATAL: Wrong Python selected" in text
    assert "3.11" in text
    assert "Environment.GetEnvironmentVariable(\"PATH\")" not in text
