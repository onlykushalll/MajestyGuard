import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
UNINSTALLERS = [
    ROOT / "Uninstall.ps1",
    ROOT / "build" / "staged" / "Uninstall.ps1",
]


def test_uninstall_preserves_user_data_by_default_and_requires_explicit_removal():
    for script in UNINSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "#Requires -RunAsAdministrator" not in text
        assert "SupportsShouldProcess" in text
        assert "[switch]$RemoveUserData" in text
        assert "Preserving user data" in text
        assert "if ($RemoveUserData)" in text


def test_uninstall_whatif_runs_without_admin_and_does_not_remove_local_appdata(tmp_path):
    script = ROOT / "build" / "staged" / "Uninstall.ps1"
    local_app_data = tmp_path / "LocalAppData"
    user_data = local_app_data / "MajestyGuard"
    user_data.mkdir(parents=True)
    marker = user_data / "embeddings_v2.npy"
    marker.write_text("keep", encoding="utf-8")

    env = os.environ.copy()
    env["ProgramFiles"] = str(tmp_path / "ProgramFiles")
    env["LOCALAPPDATA"] = str(local_app_data)
    env["APPDATA"] = str(tmp_path / "Roaming")

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-WhatIf",
        ],
        cwd=str(ROOT / "build" / "staged"),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert marker.read_text(encoding="utf-8") == "keep"
    assert "Preserving user data" in result.stdout
