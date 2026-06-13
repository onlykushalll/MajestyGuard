from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_startup_registers_user_task_with_safe_daemon_flags():
    script = (ROOT / "setup" / "install_startup.ps1").read_text(encoding="utf-8")

    assert "Register-ScheduledTask" in script
    assert "Install-StartupFolderFallback" in script
    assert "MajestyGuard_UserDaemon.cmd" in script
    assert "MajestyGuard_UserDaemon" in script
    assert "pythonw.exe" in script
    assert "MG_ENABLE_LOCK=0" in script
    assert "MG_IDLE_TIMEOUT=90" in script
    assert "MG_PASSIVE_FPS=0" in script
    assert "MG_OVERLAY_WATCHDOG=1" in script
    assert "MG_ENABLE_WHCDF_IPC=0" in script
    assert "MG_ENABLE_SERVICE_IPC=0" in script
    assert "MG_ADAFACE_FLIP_FUSION=1" in script
    assert "RunLevel Highest" not in script
    assert "Administrator" not in script
    assert "f2edf" not in script.lower()


def test_uninstall_startup_removes_only_user_task():
    script = (ROOT / "setup" / "uninstall_startup.ps1").read_text(encoding="utf-8")

    assert "Unregister-ScheduledTask" in script
    assert "MajestyGuard_UserDaemon.cmd" in script
    assert "MajestyGuard_UserDaemon" in script
    assert "Run as Administrator" not in script


def test_setup_script_runs_policy_audit_before_installing_startup():
    script = (ROOT / "setup" / "setup.ps1").read_text(encoding="utf-8")

    assert "mg_policy_audit.py" in script
    assert "MG_ENABLE_LOCK = \"0\"" in script
    assert "MG_OVERLAY_WATCHDOG = \"1\"" in script
    assert "--allow-lock-enabled" not in script
    assert "install_startup.ps1" in script
    assert script.index("mg_policy_audit.py") < script.index("install_startup.ps1")
