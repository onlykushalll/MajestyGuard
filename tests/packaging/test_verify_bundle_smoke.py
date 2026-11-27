import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

verify_bundle_smoke = SimpleNamespace(**runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "packaging" / "verify_bundle_smoke.py")
))


def test_environment_is_isolated_and_inbound_flags_are_ignored(tmp_path):
    env = verify_bundle_smoke.smoke_env(tmp_path, {
        "Path": "keep", "PYTHONPATH": "drop", "_PYI_APPLICATION_HOME_DIR": "old",
        "MG_ENABLE_LOCK": "1", "MG_FORCE_LOCK_STARTUP": "1", "MG_PROFILE_PIPELINE": "1",
    })
    assert env["Path"] == "keep" and env["MG_ENABLE_LOCK"] == "0"
    assert env["LOCALAPPDATA"] == str(tmp_path)
    assert not ({"PYTHONPATH", "_PYI_APPLICATION_HOME_DIR", "MG_FORCE_LOCK_STARTUP", "MG_PROFILE_PIPELINE"} & env.keys())


def test_smoke_logs_exits_and_times_out(tmp_path):
    env = verify_bundle_smoke.smoke_env(tmp_path)
    run = verify_bundle_smoke.run_check
    assert run("ok", [sys.executable, "-c", "print('ok')"], tmp_path, env, 5) is None
    assert "ok" in (tmp_path / "ok.log").read_text()
    assert "exit 7" in run("bad", [sys.executable, "-c", "exit(7)"], tmp_path, env, 5)
    assert "timed out" in run("slow", [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, env, .3)
