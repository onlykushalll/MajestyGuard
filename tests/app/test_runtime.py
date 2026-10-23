from pathlib import Path

from app.config import RuntimeSettings, load_settings, save_settings
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


def test_pause_stops_owned_daemon_and_persists_paused_state(tmp_path):
    settings_path = tmp_path / "settings.json"
    supervisor = Supervisor(tmp_path, Path("python.exe"), settings_path)
    process = FakeProcess()
    supervisor._process = process

    supervisor.pause()

    assert process.terminated is True
    assert supervisor.status() is RuntimeStatus.PAUSED
    assert load_settings(settings_path) == RuntimeSettings(idle_timeout_seconds=60, paused=True)


def test_resume_clears_paused_state_before_starting(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    save_settings(settings_path, RuntimeSettings(idle_timeout_seconds=120, paused=True))
    entrypoint = tmp_path / "daemon" / "main.py"
    entrypoint.parent.mkdir()
    entrypoint.write_text("# test entrypoint", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), settings_path)
    monkeypatch.setattr(supervisor, "_spawn", lambda *_args: FakeProcess())

    assert supervisor.resume() is True
    assert supervisor.status() is RuntimeStatus.RUNNING
    assert load_settings(settings_path) == RuntimeSettings(idle_timeout_seconds=120, paused=False)


def test_set_idle_timeout_rejects_out_of_policy_value(tmp_path):
    settings_path = tmp_path / "settings.json"
    supervisor = Supervisor(tmp_path, Path("python.exe"), settings_path)

    assert supervisor.set_idle_timeout(1) is False
    assert load_settings(settings_path) == RuntimeSettings()


def test_set_idle_timeout_persists_policy_bounded_value(tmp_path):
    settings_path = tmp_path / "settings.json"
    supervisor = Supervisor(tmp_path, Path("python.exe"), settings_path)

    assert supervisor.set_idle_timeout(180) is True
    assert load_settings(settings_path) == RuntimeSettings(idle_timeout_seconds=180, paused=False)


def test_supervisor_uses_packaged_daemon_and_ui_when_both_are_available(tmp_path, monkeypatch):
    launched = {}
    daemon_executable = tmp_path / "MajestyGuardDaemon.exe"
    ui_executable = tmp_path / "MajestyGuardUI.exe"
    daemon_executable.write_bytes(b"daemon")
    ui_executable.write_bytes(b"ui")
    supervisor = Supervisor(
        tmp_path,
        Path("python.exe"),
        tmp_path / "settings.json",
        daemon_executable=daemon_executable,
    )
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda command, env: launched.update(command=command, env=env) or FakeProcess(),
    )

    assert supervisor.start() is True
    assert launched["command"] == [str(daemon_executable)]
    assert launched["env"]["MG_UI_EXECUTABLE"] == str(ui_executable)


def test_supervisor_uses_packaged_sibling_folder_layout(tmp_path, monkeypatch):
    launched = {}
    daemon_executable = tmp_path / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe"
    ui_executable = tmp_path / "MajestyGuardUI" / "MajestyGuardUI.exe"
    daemon_executable.parent.mkdir()
    ui_executable.parent.mkdir()
    daemon_executable.write_bytes(b"daemon")
    ui_executable.write_bytes(b"ui")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda command, env: launched.update(command=command, env=env) or FakeProcess(),
    )

    assert supervisor.start() is True
    assert launched["command"] == [str(daemon_executable)]
    assert launched["env"]["MG_UI_EXECUTABLE"] == str(ui_executable)


def test_start_enrollment_uses_packaged_daemon_when_present(tmp_path, monkeypatch):
    launched = {}
    packaged_daemon = tmp_path / "MajestyGuardDaemon.exe"
    packaged_daemon.write_bytes(b"binary")
    supervisor = Supervisor(
        tmp_path,
        Path("python.exe"),
        tmp_path / "settings.json",
        daemon_executable=packaged_daemon,
    )
    monkeypatch.setattr(
        supervisor,
        "_spawn_enrollment",
        lambda command, env: launched.update(command=command, env=env) or FakeProcess(),
    )

    assert supervisor.start_enrollment() is True
    assert launched["command"] == [str(packaged_daemon), "--enroll"]
    assert launched["env"]["MG_ENABLE_LOCK"] == "0"

def test_start_enrollment_stops_running_daemon_and_resumes_after_completion(tmp_path, monkeypatch):
    daemon_entry = tmp_path / "daemon" / "main.py"
    enroll_entry = tmp_path / "daemon" / "enroll_v2.py"
    daemon_entry.parent.mkdir(parents=True)
    daemon_entry.write_text("# daemon", encoding="utf-8")
    enroll_entry.write_text("# enroll", encoding="utf-8")
    local_app_data = tmp_path / "local"
    gallery = local_app_data / "MajestyGuard" / "embeddings_v2.npy"
    gallery.parent.mkdir(parents=True)
    gallery.write_bytes(b"enrolled")

    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json", local_app_data=local_app_data)
    running_daemon = FakeProcess(returncode=None)
    enroll_proc = FakeProcess(returncode=None)
    supervisor._process = running_daemon
    monkeypatch.setattr(supervisor, "_spawn_enrollment", lambda *_args: enroll_proc)
    monkeypatch.setattr(supervisor, "_spawn", lambda *_args: FakeProcess(returncode=None))

    assert supervisor.start_enrollment() is True
    assert running_daemon.terminated is True
    assert supervisor._process is None

    # Simulate enrollment finishing -> next status() poll auto-resumes protection
    enroll_proc.returncode = 0
    assert supervisor.status() is RuntimeStatus.RUNNING
