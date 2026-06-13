from pathlib import Path
import re


def test_run_daemon_batch_is_lock_disabled_and_time_bounded_by_default():
    script = (Path(__file__).resolve().parents[1] / "run_daemon.bat").read_text(encoding="utf-8")

    assert r"C:\tmp\MajestyGuard-v2" not in script
    assert "%~dp0daemon\\main.py" in script
    assert "%~dp0daemon\\mg_policy_audit.py" in script
    assert "SET MG_ENABLE_LOCK=0" in script
    assert "SET MG_IDLE_TIMEOUT=90" in script
    assert "SET MG_PASSIVE_FPS=0" in script
    assert "SET MG_SOFT_LOCK_VERIFY_WINDOW_SECONDS=12" in script
    assert "SET MG_ENABLE_WHCDF_IPC=0" in script
    assert "SET MG_ENABLE_SERVICE_IPC=0" in script
    assert "SET MG_ADAFACE_FLIP_FUSION=1" in script
    match = re.search(r'IF "%MG_MAX_SECONDS%"=="" SET MG_MAX_SECONDS=(\d+)', script)
    assert match is not None
    assert int(match.group(1)) > 0
    assert "mg_policy_audit.py" in script
    assert "--require-bound" in script
    assert script.index("mg_policy_audit.py") < script.index("CAMERA / RECOGNITION STARTING")
    assert script.index("mg_policy_audit.py") < script.index('"%VENV%" "%DAEMON%"')


def test_run_ui_batch_uses_moved_repo_relative_ui_path():
    script = (Path(__file__).resolve().parents[1] / "run_ui.bat").read_text(encoding="utf-8")

    assert r"C:\tmp\MajestyGuard-v2" not in script
    assert "%~dp0ui\\main.py" in script
