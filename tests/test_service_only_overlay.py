from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_CONFIG = ROOT / "src" / "MajestyGuard.Core" / "Models" / "AppConfig.cs"
WORKER = ROOT / "src" / "MajestyGuard.Service" / "Worker.cs"
INSTALLER = ROOT / "Install.ps1"
RUNNER = ROOT / "run_phase3_admin.ps1"


def test_service_config_can_disable_service_launched_overlay():
    config_text = APP_CONFIG.read_text(encoding="utf-8")
    worker_text = WORKER.read_text(encoding="utf-8")

    assert "EnableOverlayLaunch" in config_text
    assert "EnableOverlayLaunch" in worker_text
    assert "Service overlay launch disabled by config" in worker_text
    assert "if (_config.EnableOverlayLaunch)" in worker_text


def test_service_only_admin_runner_disables_overlay_launch():
    installer_text = INSTALLER.read_text(encoding="utf-8")
    runner_text = RUNNER.read_text(encoding="utf-8")

    assert "[switch]$DisableServiceOverlayLaunch" in installer_text
    assert 'Set-JsonProperty -Target $configJson -Name "EnableOverlayLaunch" -Value $false' in installer_text
    assert "DisableServiceOverlayLaunch" in runner_text
    assert "$installArgs.DisableServiceOverlayLaunch = $true" in runner_text
