import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from app.config import RuntimeSettings, load_settings, save_settings
from app.diagnostics import ResourceSampler
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
    monkeypatch.setenv("MG_FORCE_LOCK_STARTUP", "1")
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
    assert "MG_FORCE_LOCK_STARTUP" not in launched["env"]
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


def test_pause_reaps_owned_helpers_before_reporting_paused(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    daemon = FakeProcess()
    helper = FakeProcess()
    supervisor._process = daemon
    monkeypatch.setattr(supervisor, "_owned_daemon_children", lambda: [helper], raising=False)
    assert supervisor.pause()
    assert daemon.terminated
    assert helper.killed
    assert supervisor.status() is RuntimeStatus.PAUSED


def test_failed_pause_keeps_worker_tracking_and_reports_failure(tmp_path):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    process = FakeProcess()
    supervisor._process = process

    def deny():
        raise PermissionError("cannot stop worker")

    process.terminate = deny
    assert supervisor.pause() is False
    assert supervisor._process is process
    assert not load_settings(tmp_path / "settings.json").paused
    assert supervisor.status() is RuntimeStatus.RUNNING


@pytest.mark.parametrize("action, expected", [
    ("pause", RuntimeStatus.PAUSED),
    ("stop", RuntimeStatus.STOPPED),
])
def test_stop_and_pause_release_real_worker_tree(tmp_path, action, expected):
    marker = tmp_path / "helper.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "marker = pathlib.Path(sys.argv[1]); pending = marker.with_suffix('.tmp'); "
        "pending.write_text(str(helper.pid), encoding='ascii'); pending.replace(marker); time.sleep(60)"
    )
    worker = subprocess.Popen([sys.executable, "-c", parent_code, str(marker)],
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    supervisor = Supervisor(tmp_path, Path(sys.executable), tmp_path / "settings.json")
    supervisor._process = worker
    helper = None
    try:
        until = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < until:
            time.sleep(0.03)
        assert marker.is_file(), "Test worker did not publish its helper PID"
        helper = psutil.Process(int(marker.read_text()))
        sampler = ResourceSampler()
        running = sampler.sample(supervisor.diagnostic_processes())
        assert {item.pid for item in running if item.group == "Protection"} >= {worker.pid, helper.pid}
        assert all(item.ram_bytes > 0 for item in running)
        assert getattr(supervisor, action)()
        assert worker.poll() is not None and not helper.is_running()
        assert all(item.group == "Control Center" for item in sampler.sample(supervisor.diagnostic_processes()))
        assert supervisor.status() is expected
    finally:
        if helper is None and worker.poll() is None:
            for child in psutil.Process(worker.pid).children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        if helper and helper.is_running():
            helper.kill()
            helper.wait(timeout=5)
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)


def test_start_enrollment_refuses_a_second_concurrent_enrollment(tmp_path, monkeypatch):
    enrollment_entry = tmp_path / "daemon" / "enroll_continuous.py"
    enrollment_entry.parent.mkdir()
    enrollment_entry.write_text("# test entrypoint", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    supervisor._enrollment_process = FakeProcess()

    assert supervisor.start_enrollment() is False


def test_failed_resume_keeps_paused_state(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(path, RuntimeSettings(paused=True))
    supervisor = Supervisor(tmp_path, Path("python.exe"), path)
    assert not supervisor.resume()
    assert supervisor.status() is RuntimeStatus.PAUSED


def test_start_does_not_compete_with_an_open_enrollment_window(tmp_path, monkeypatch):
    supervisor, _, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    supervisor.stop()
    supervisor._enrollment_process = FakeProcess()
    assert not supervisor.start()
    assert len(spawned) == 1


def test_windows_lock_setting_is_rolled_back_if_restart_fails(tmp_path, monkeypatch):
    supervisor, _, _ = _recovering_supervisor(tmp_path, monkeypatch)
    attempted = []

    def spawn(_command, environment):
        attempted.append(environment["MG_ENABLE_LOCK"])
        if attempted[-1] == "1":
            raise OSError("new config could not launch")
        return FakeProcess()

    monkeypatch.setattr(supervisor, "_spawn", spawn)
    assert not supervisor.set_windows_lock_enabled(True)
    assert attempted == ["1", "0"]
    assert not supervisor.windows_lock_enabled()
    assert supervisor.status() is RuntimeStatus.RUNNING


def test_start_enrollment_uses_continuous_coverage_script(tmp_path, monkeypatch):
    launched = {}
    enrollment_entry = tmp_path / "daemon" / "enroll_continuous.py"
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
    enroll_entry = tmp_path / "daemon" / "enroll_continuous.py"
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


def test_start_enrollment_failure_does_not_leave_daemon_stopped(tmp_path, monkeypatch):
    """No enrollment script: the running daemon must be left alone."""
    daemon_entry = tmp_path / "daemon" / "main.py"
    daemon_entry.parent.mkdir(parents=True)
    daemon_entry.write_text("# daemon", encoding="utf-8")  # no enroll_continuous.py
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    running_daemon = FakeProcess(returncode=None)
    supervisor._process = running_daemon

    assert supervisor.start_enrollment() is False
    assert running_daemon.terminated is False
    assert supervisor.status() is RuntimeStatus.RUNNING


def test_start_enrollment_spawn_failure_restores_the_daemon(tmp_path, monkeypatch):
    daemon_entry = tmp_path / "daemon" / "main.py"
    enroll_entry = tmp_path / "daemon" / "enroll_continuous.py"
    daemon_entry.parent.mkdir(parents=True)
    daemon_entry.write_text("# daemon", encoding="utf-8")
    enroll_entry.write_text("# enroll", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    old_daemon = FakeProcess(returncode=None)
    supervisor._process = old_daemon

    def _boom(*_args):
        raise OSError("cannot spawn")

    monkeypatch.setattr(supervisor, "_spawn_enrollment", _boom)
    monkeypatch.setattr(supervisor, "_spawn", lambda *_args: FakeProcess(returncode=None))

    assert supervisor.start_enrollment() is False
    assert old_daemon.terminated is True
    assert supervisor.status() is RuntimeStatus.RUNNING  # protection restored
    assert supervisor._resume_after_enrollment is False


def _locked_supervisor(tmp_path, *, alive=True):
    state_file = tmp_path / "lock_state.txt"
    state_file.write_text("LOCKED\n", encoding="utf-8")
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    process = FakeProcess(returncode=None if alive else 1)
    supervisor._process = process
    return supervisor, process, state_file


def test_pause_is_refused_while_the_session_is_locked(tmp_path):
    supervisor, process, _ = _locked_supervisor(tmp_path)

    assert supervisor.controls_locked() is True
    assert supervisor.pause() is False
    assert process.terminated is False
    assert supervisor._process is process
    assert load_settings(tmp_path / "settings.json").paused is False


def test_stop_and_settings_changes_are_refused_while_locked(tmp_path):
    supervisor, process, _ = _locked_supervisor(tmp_path)

    assert supervisor.stop() is False
    assert supervisor.set_idle_timeout(300) is False
    assert supervisor.set_windows_lock_enabled(True) is False
    assert process.terminated is False and process.killed is False
    settings = load_settings(tmp_path / "settings.json")
    assert settings.idle_timeout_seconds == 60
    assert settings.enable_windows_lock is False


def test_enrollment_does_not_stop_a_locked_daemon(tmp_path, monkeypatch):
    supervisor, process, _ = _locked_supervisor(tmp_path)
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "enroll_continuous.py").write_text("# enroll", encoding="utf-8")
    spawned = []
    monkeypatch.setattr(supervisor, "_spawn_enrollment", lambda *a: spawned.append(a) or FakeProcess())

    assert supervisor.start_enrollment() is False
    assert spawned == []
    assert process.terminated is False


def test_pause_when_unlocked_stops_the_daemon_and_clears_stale_lock_state(tmp_path):
    supervisor, process, state_file = _locked_supervisor(tmp_path, alive=True)
    state_file.write_text("UNLOCKED\n", encoding="utf-8")

    assert supervisor.controls_locked() is False
    assert supervisor.pause() is True
    assert process.terminated is True
    assert load_settings(tmp_path / "settings.json").paused is True
    assert state_file.read_text(encoding="utf-8").strip() == "UNLOCKED"


def test_stale_locked_marker_with_a_dead_daemon_does_not_strand_controls(tmp_path):
    supervisor, _, state_file = _locked_supervisor(tmp_path, alive=False)

    assert supervisor.controls_locked() is False
    assert supervisor.pause() is True
    assert state_file.read_text(encoding="utf-8").strip() == "UNLOCKED"


def test_stop_rechecks_the_lock_right_before_terminating(tmp_path, monkeypatch):
    """Lock engages between stop()'s first check and the terminate call: daemon must survive."""
    supervisor, process, state_file = _locked_supervisor(tmp_path)
    state_file.write_text("UNLOCKED\n", encoding="utf-8")
    answers = iter([False, True])
    monkeypatch.setattr(supervisor, "controls_locked", lambda: next(answers))

    assert supervisor.stop() is False
    assert process.terminated is False
    assert supervisor._process is process


def test_pause_that_loses_the_race_leaves_settings_unpaused(tmp_path, monkeypatch):
    supervisor, process, state_file = _locked_supervisor(tmp_path)
    state_file.write_text("UNLOCKED\n", encoding="utf-8")
    answers = iter([False, False, True])      # pause check, stop check, stop re-check
    monkeypatch.setattr(supervisor, "controls_locked", lambda: next(answers))

    assert supervisor.pause() is False
    assert process.terminated is False
    assert load_settings(tmp_path / "settings.json").paused is False


# ---- automatic recovery ------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _recovering_supervisor(tmp_path, monkeypatch, *, enrolled=True):
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "main.py").write_text("#", encoding="utf-8")
    state = tmp_path / "MajestyGuard"
    state.mkdir()
    if enrolled:
        (state / "embeddings_v2.npy").write_bytes(b"x")
    clock = Clock()
    sup = Supervisor(tmp_path, Path("python.exe"), state / "settings.json",
                     local_app_data=tmp_path, clock=clock)
    spawned = []
    monkeypatch.setattr(sup, "_spawn", lambda c, e: spawned.append(FakeProcess()) or spawned[-1])
    assert sup.start()
    return sup, clock, spawned


def test_dead_daemon_is_restarted_after_a_short_pause(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    assert sup.maintain() == ""                       # healthy: nothing to do
    spawned[0].returncode = 1                         # crash
    assert sup.maintain() == "waiting" and len(spawned) == 1
    clock.now += 2.1
    assert sup.maintain() == "restarted" and len(spawned) == 2
    assert sup.status() is RuntimeStatus.RUNNING
    assert "restarted automatically" in sup.recovery_note()


def test_transient_spawn_failure_does_not_lose_the_pending_recovery(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    real_spawn = sup._spawn
    attempts = []

    def unreliable_spawn(command, environment):
        attempts.append(clock.now)
        if len(attempts) == 1:
            raise OSError("could not launch worker")
        return real_spawn(command, environment)

    monkeypatch.setattr(sup, "_spawn", unreliable_spawn)
    spawned[-1].returncode = 1
    assert sup.maintain() == "waiting"
    clock.now += 2
    assert sup.maintain() == "waiting"
    assert sup.status() is RuntimeStatus.DEGRADED
    assert sup.maintain() == "waiting"
    clock.now += 5
    assert sup.maintain() == "restarted"
    assert len(attempts) == 2


def test_repeated_spawn_failures_exhaust_the_same_recovery_budget(tmp_path, monkeypatch):
    from app.runtime import MAX_CRASHES_IN_WINDOW, RECOVERY_DELAYS_S

    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    attempts = []

    def fail_spawn(command, environment):
        attempts.append(clock.now)
        raise OSError("could not launch worker")

    monkeypatch.setattr(sup, "_spawn", fail_spawn)
    spawned[-1].returncode = 1
    assert sup.maintain() == "waiting"
    for n in range(MAX_CRASHES_IN_WINDOW):
        clock.now += RECOVERY_DELAYS_S[min(n, len(RECOVERY_DELAYS_S) - 1)]
        assert sup.maintain() == ("waiting" if n + 1 < MAX_CRASHES_IN_WINDOW else "gave_up")
    assert sup.maintain() == "gave_up"
    assert len(attempts) == MAX_CRASHES_IN_WINDOW


def test_restart_pauses_grow_with_repeated_crashes(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    for expected_delay in (2.0, 5.0, 15.0):
        spawned[-1].returncode = 1
        assert sup.maintain() == "waiting"
        clock.now += expected_delay - 0.5
        assert sup.maintain() == "waiting"            # not yet
        clock.now += 0.6
        assert sup.maintain() == "restarted"


def test_crash_loop_gives_up_and_a_manual_start_forgives(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    from app.runtime import RECOVERY_DELAYS_S

    for n in range(1, 9):
        spawned[-1].returncode = 1
        result = sup.maintain()
        if result == "gave_up":
            break
        clock.now += RECOVERY_DELAYS_S[min(n, len(RECOVERY_DELAYS_S)) - 1] + 0.1
        assert sup.maintain() == "restarted"
    assert result == "gave_up"
    count = len(spawned)
    clock.now += 500
    assert sup.maintain() == "gave_up" and len(spawned) == count      # stays stopped
    assert "keeps stopping" in sup.recovery_note()
    assert sup.start() is True                                          # user presses Restart
    assert sup.maintain() == ""
    spawned[-1].returncode = 1
    assert sup.maintain() == "waiting"                                  # recovery works again


def test_stable_run_clears_crash_history(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    spawned[-1].returncode = 1
    sup.maintain()
    clock.now += 2.1
    assert sup.maintain() == "restarted"
    clock.now += 200                                  # ran fine for a while
    assert sup.maintain() == ""
    spawned[-1].returncode = 1
    assert sup.maintain() == "waiting"
    clock.now += 2.1                                  # back to the shortest pause, not the next step
    assert sup.maintain() == "restarted"


def test_user_stopped_or_paused_daemon_is_never_restarted(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch)
    assert sup.stop() is True
    clock.now += 500
    assert sup.maintain() == "" and len(spawned) == 1
    assert sup.start()
    spawned[-1].returncode = 1
    sup.pause()
    assert sup.maintain() == "" and len(spawned) == 2


def test_no_restart_without_an_enrollment(tmp_path, monkeypatch):
    sup, clock, spawned = _recovering_supervisor(tmp_path, monkeypatch, enrolled=False)
    spawned[0].returncode = 1
    clock.now += 500
    assert sup.maintain() == "" and len(spawned) == 1


def test_start_over_is_the_only_way_to_waive_identity_continuity(tmp_path, monkeypatch):
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "enroll_continuous.py").write_text("#", encoding="utf-8")
    sup = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json", local_app_data=tmp_path)
    seen = []
    monkeypatch.setattr(sup, "_spawn_enrollment", lambda c, e: seen.append(e["MG_ENROLL_CONTINUITY"]) or FakeProcess())
    monkeypatch.setenv("MG_ENROLL_CONTINUITY", "0")          # a hostile environment must not leak in
    assert sup.start_enrollment() is True
    sup._enrollment_process = None
    assert sup.start_enrollment(start_over=True) is True
    assert seen == ["1", "0"]
