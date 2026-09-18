from pathlib import Path

from app.runtime import RuntimeStatus, Supervisor


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_supervisor_launches_daemon_with_safe_daily_use_environment(tmp_path, monkeypatch):
    launched = {}
    daemon_entry = tmp_path / "daemon" / "main.py"
    daemon_entry.parent.mkdir()
    daemon_entry.write_text("# test entrypoint", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda command, env: launched.update(command=command, env=env) or FakeProcess(),
    )

    assert supervisor.start() is True
    assert launched["command"] == ["python.exe", str(tmp_path / "daemon" / "main.py")]
    assert launched["env"]["MG_ENABLE_LOCK"] == "0"
    assert launched["env"]["MG_MAX_SECONDS"] == "0"
    assert launched["env"]["MG_OVERLAY_WATCHDOG"] == "1"
    assert launched["env"]["MG_IDLE_TIMEOUT"] == "60"
    assert supervisor.status() is RuntimeStatus.RUNNING


def test_supervisor_fails_closed_when_daemon_entrypoint_is_missing(tmp_path):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")

    assert supervisor.start() is False
    assert supervisor.status() is RuntimeStatus.STOPPED


def test_supervisor_reports_degraded_when_child_exits_unexpectedly(tmp_path):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    supervisor._process = FakeProcess(returncode=1)

    assert supervisor.status() is RuntimeStatus.DEGRADED


def test_supervisor_detects_existing_v2_enrollment_from_current_user_data(tmp_path):
    local_app_data = tmp_path / "local"
    enrollment = local_app_data / "MajestyGuard" / "embeddings_v2.npy"
    enrollment.parent.mkdir(parents=True)
    enrollment.write_bytes(b"gallery")

    supervisor = Supervisor(
        tmp_path,
        Path("python.exe"),
        tmp_path / "settings.json",
        local_app_data=local_app_data,
    )

    assert supervisor.enrollment_present() is True


def test_supervisor_stops_its_owned_daemon(tmp_path):
    process = FakeProcess()
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    supervisor._process = process

    supervisor.stop()

    assert process.terminated is True
    assert supervisor.status() is RuntimeStatus.STOPPED


def test_start_enrollment_refuses_a_second_concurrent_enrollment(tmp_path, monkeypatch):
    enrollment_entry = tmp_path / "daemon" / "enroll_v2.py"
    enrollment_entry.parent.mkdir()
    enrollment_entry.write_text("# test entrypoint", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    supervisor._enrollment_process = FakeProcess()

    assert supervisor.start_enrollment() is False


def test_start_enrollment_uses_existing_guided_script(tmp_path, monkeypatch):
    launched = {}
    enrollment_entry = tmp_path / "daemon" / "enroll_v2.py"
    enrollment_entry.parent.mkdir()
    enrollment_entry.write_text("# test entrypoint", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(
        supervisor,
        "_spawn_enrollment",
        lambda command, env: launched.update(command=command, env=env) or FakeProcess(),
    )

    assert supervisor.start_enrollment() is True
    assert launched["command"] == ["python.exe", str(enrollment_entry)]
    assert launched["env"]["MG_ENABLE_LOCK"] == "0"


def test_open_log_directory_returns_current_user_runtime_path(tmp_path):
    local_app_data = tmp_path / "local"
    supervisor = Supervisor(
        tmp_path,
        Path("python.exe"),
        tmp_path / "settings.json",
        local_app_data=local_app_data,
    )

    assert supervisor.open_log_directory() == local_app_data / "MajestyGuard"
