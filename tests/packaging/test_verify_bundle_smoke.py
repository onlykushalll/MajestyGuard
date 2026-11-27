import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[2] / "packaging" / "verify_bundle_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mg_verify_bundle_smoke", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mg_verify_bundle_smoke"] = module
    spec.loader.exec_module(module)
    return module


def _make_fake_bundle(root: Path) -> Path:
    bundle = root / "MajestyGuard"
    (bundle / "MajestyGuard").mkdir(parents=True)
    (bundle / "MajestyGuardDaemon").mkdir(parents=True)
    (bundle / "MajestyGuardUI").mkdir(parents=True)
    for rel in (
        "MajestyGuard/MajestyGuard.exe",
        "MajestyGuardDaemon/MajestyGuardDaemon.exe",
        "MajestyGuardUI/MajestyGuardUI.exe",
    ):
        (bundle / rel).write_bytes(b"stub")
    return bundle


def test_build_smoke_env_isolates_state_and_strips_venv_leaks(tmp_path):
    mod = _load_module()
    venv = tmp_path / "build-venv"
    venv_scripts = venv / "Scripts"
    venv_scripts.mkdir(parents=True)
    smoke_state = tmp_path / "smoke-state"

    env = mod.build_smoke_env(
        smoke_state,
        base_env={
            "PATH": os.pathsep.join([str(venv_scripts), r"C:\Windows\System32"]),
            "VIRTUAL_ENV": str(venv),
            "PYTHONPATH": str(tmp_path / "repo"),
            "PYTHONHOME": str(venv),
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            "_MEIPASS": str(tmp_path / "meipass"),
            "MG_ENABLE_LOCK": "1",
            "MG_ENABLE_WHCDF_IPC": "1",
            "MG_ENABLE_SERVICE_IPC": "1",
        },
    )

    assert env["MG_ENABLE_LOCK"] == "0"
    assert env["MG_ENABLE_WHCDF_IPC"] == "0"
    assert env["MG_ENABLE_SERVICE_IPC"] == "0"
    assert env["LOCALAPPDATA"] == str(smoke_state.resolve())
    assert env["MPLCONFIGDIR"] == str((smoke_state / "mplconfig").resolve())
    assert env["PYTHONUNBUFFERED"] == "1"
    for leaked in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYINSTALLER_RESET_ENVIRONMENT", "_MEIPASS"):
        assert leaked not in env
    assert str(venv_scripts) not in env["PATH"]
    assert r"C:\Windows\System32" in env["PATH"]


def test_enrollment_nonzero_exit_reports_missing_module_from_daemon_log(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    smoke_state = tmp_path / "smoke-state"
    log_file = smoke_state / "MajestyGuard" / "daemon.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("2026-11-27 00:00:00,000 INFO old: stale entry from prior run\n", encoding="utf-8")

    check = mod.SmokeCheck(
        name="enrollment",
        exe=bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        args=("--enroll", "--self-test"),
        timeout_s=10.0,
    )

    def fake_popen(_cmd, stdout=None, stderr=None, **_kw):
        # Appends only new log entries for this run; stale prior lines must be excluded.
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-11-27 12:00:01,000 INFO majestyguard.enrollment: Loading face model\n"
                "2026-11-27 12:00:02,000 INFO MajestyGuard.Attention: Applied providers: ['CPUExecutionProvider']\n"
                "2026-11-27 12:00:03,000 ERROR majestyguard.daemon: Face setup could not initialize\n"
                "Traceback (most recent call last):\n"
                "ModuleNotFoundError: No module named 'scipy._cyutility'\n"
            )
        stderr.write(b"stderr diagnostic detail\n")
        stderr.flush()

        class _Proc:
            pid = 4242

            def poll(self):
                return 1

            def wait(self, timeout=None):
                return 1

        return _Proc()

    result = mod.run_check(
        check,
        smoke_state_dir=smoke_state,
        env=mod.build_smoke_env(smoke_state, base_env={}),
        popen_fn=fake_popen,
        tree_inspector=lambda _pid: [],
        dialog_inspector=lambda _pids: None,
    )

    assert not result.passed
    assert result.returncode == 1
    assert "stale entry" not in result.daemon_log_delta
    assert "scipy._cyutility" in result.daemon_log_delta
    report = mod.format_failure_report(result)
    assert "Packaged enrollment check FAILED" in report
    assert "--hidden-import scipy._cyutility" in report
    assert "ONNX Runtime CPUExecutionProvider" not in report
    assert "stderr diagnostic detail" in report


def test_blocking_modal_dialog_aborts_early_and_reports_dialog_text(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    smoke_state = tmp_path / "smoke-state"
    check = mod.SmokeCheck(
        name="enrollment",
        exe=bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        args=("--enroll", "--self-test"),
        timeout_s=90.0,
    )

    terminated = []

    class _HungProc:
        pid = 9001

        def __init__(self):
            self.alive = True

        def poll(self):
            return None if self.alive else 1

        def kill(self):
            self.alive = False

        def wait(self, timeout=None):
            self.alive = False
            return 1

    proc = _HungProc()
    dialog = mod.ModalDialogInfo(
        pid=9001,
        hwnd=123456,
        title="Unhandled exception in script",
        body="Failed to execute script 'main' due to unhandled exception: No module named 'scipy._cyutility'",
    )

    def fake_terminate(target_proc, nodes):
        terminated.append((target_proc.pid, [n.pid for n in nodes]))
        target_proc.kill()

    result = mod.run_check(
        check,
        smoke_state_dir=smoke_state,
        env=mod.build_smoke_env(smoke_state, base_env={}),
        poll_interval_s=0.0,
        popen_fn=lambda *_a, **_kw: proc,
        tree_inspector=lambda pid: [mod.ProcessNode(pid=pid, ppid=1, exe_name="MajestyGuardDaemon.exe")],
        dialog_inspector=lambda pids: dialog if 9001 in pids else None,
        tree_terminator=fake_terminate,
    )

    assert not result.passed
    assert not result.timed_out
    assert result.modal_dialog == dialog
    assert terminated == [(9001, [9001])]
    report = mod.format_failure_report(result)
    assert "BLOCKED BY DIALOG" in report
    assert "Unhandled exception in script" in report
    assert "--hidden-import scipy._cyutility" in report


def test_dll_ntstatus_exit_code_and_phase_timeout_diagnoses(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    check = mod.SmokeCheck(
        name="enrollment",
        exe=bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        args=("--enroll", "--self-test"),
        timeout_s=90.0,
    )

    dll_res = mod.SmokeCheckResult(
        check=check,
        passed=False,
        elapsed_s=1.2,
        returncode=-1073741515,
        diagnoses=mod.diagnose_failure(
            check=check,
            returncode=-1073741515,
            timed_out=False,
            missing_exe=False,
            modal_dialog=None,
            active_tree=(),
            stdout_text="",
            stderr_text="",
            daemon_log_delta="",
        ),
    )
    dll_report = mod.format_failure_report(dll_res)
    assert "0xC0000135: STATUS_DLL_NOT_FOUND" in dll_report
    assert "install_bundle_crt.ps1" in dll_report

    stalled_log = (
        "2026-11-27 12:00:01,000 INFO majestyguard.enrollment: Loading face model\n"
        "2026-11-27 12:00:03,000 INFO majestyguard.enrollment: Loading liveness model\n"
        "2026-11-27 12:00:04,000 INFO MajestyGuard.DepthLiveness: MiDaS depth model loaded: midas_v21_small_256.onnx\n"
    )
    stall_diagnoses = mod.diagnose_failure(
        check=check,
        returncode=1,
        timed_out=True,
        missing_exe=False,
        modal_dialog=None,
        active_tree=[mod.ProcessNode(pid=11, ppid=1, exe_name="MajestyGuardDaemon.exe")],
        stdout_text="",
        stderr_text="",
        daemon_log_delta=stalled_log,
    )
    joined = "\n".join(stall_diagnoses)
    assert "MediaPipe FaceMesh / matplotlib font cache" in joined
    assert "MajestyGuardDaemon.exe(11)" in joined


def test_phase_stall_diagnosis_checks_combined_stdout_stderr_and_daemon_log(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    check = mod.SmokeCheck(
        name="enrollment",
        exe=bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        args=("--enroll", "--self-test"),
        timeout_s=90.0,
    )

    def _diagnose(*, stdout_text: str = "", stderr_text: str = "", daemon_log_delta: str = "") -> str:
        return "\n".join(
            mod.diagnose_failure(
                check=check,
                returncode=1,
                timed_out=True,
                missing_exe=False,
                modal_dialog=None,
                active_tree=(),
                stdout_text=stdout_text,
                stderr_text=stderr_text,
                daemon_log_delta=daemon_log_delta,
            )
        )

    face_only = _diagnose(stdout_text="Loading face model...\n")
    assert "InsightFace buffalo_l models" in face_only
    assert "No daemon.log" not in face_only

    midas_in_stderr = _diagnose(
        stdout_text="Loading face model...\nLoading liveness model...\n",
        stderr_text="INFO MajestyGuard.DepthLiveness: MiDaS depth model loaded: midas_v21_small_256.onnx\n",
    )
    assert "MediaPipe FaceMesh / matplotlib font cache" in midas_in_stderr

    attention_ready = _diagnose(
        stdout_text="Loading face model...\nLoading liveness model...\n",
        daemon_log_delta=(
            "INFO MajestyGuard.DepthLiveness: MiDaS depth model loaded\n"
            "INFO MajestyGuard.Attention: AttentionDetector ready\n"
        ),
    )
    assert "MediaPipe FaceMesh / matplotlib font cache" not in attention_ready
    assert "initializing liveness models" in attention_ready

    phase2_done = _diagnose(
        stdout_text="Loading face model...\nLoading liveness model...\n",
        daemon_log_delta=(
            "INFO MajestyGuard.DepthLiveness: MiDaS depth model loaded\n"
            "INFO MajestyGuard.Attention: AttentionDetector ready\n"
            "INFO MajestyGuard.Liveness: Phase-2 full-liveness models loaded sequentially\n"
        ),
    )
    assert "scipy.signal validation or synthetic frame analyzer.detect()" in phase2_done

    silent_stall = _diagnose()
    assert "PyInstaller unpack" in silent_stall


def test_enrollment_unrecognized_self_test_arg_reports_actionable_diagnosis(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    smoke_state = tmp_path / "smoke-state"
    check = mod.SmokeCheck(
        name="enrollment",
        exe=bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        args=("--enroll", "--self-test"),
        timeout_s=90.0,
    )

    def fake_popen(_cmd, stdout=None, stderr=None, **_kw):
        stderr.write(
            b"usage: MajestyGuardDaemon.exe [--camera CAMERA] [--dry-run]\n"
            b"MajestyGuardDaemon.exe: error: unrecognized arguments: --self-test\n"
        )
        stderr.flush()

        class _Proc:
            pid = 4343

            def poll(self):
                return 2

            def wait(self, timeout=None):
                return 2

        return _Proc()

    result = mod.run_check(
        check,
        smoke_state_dir=smoke_state,
        env=mod.build_smoke_env(smoke_state, base_env={}),
        popen_fn=fake_popen,
        tree_inspector=lambda _pid: [],
        dialog_inspector=lambda _pids: None,
    )

    assert not result.passed
    assert result.returncode == 2
    report = mod.format_failure_report(result)
    assert "daemon/enroll_continuous.py in the packaged binary lacks '--self-test' handling" in report
    assert "unrecognized arguments: --self-test" in report


def test_real_subprocess_timeout_kills_descendant_tree_and_reports_phase(tmp_path):
    mod = _load_module()
    smoke_state = tmp_path / "smoke-state"
    daemon_log = smoke_state / "MajestyGuard" / "daemon.log"
    child_pid_file = tmp_path / "child.pid"

    # Spawn a real parent -> child Python tree where the parent logs enrollment
    # phases to daemon.log and waits for the sleeping child until killed.
    parent_script = tmp_path / "parent_hang.py"
    parent_script.write_text(
        "\n".join([
            "import os, pathlib, subprocess, sys, time",
            "log_path = pathlib.Path(os.environ['LOCALAPPDATA']) / 'MajestyGuard' / 'daemon.log'",
            "log_path.parent.mkdir(parents=True, exist_ok=True)",
            "log_path.write_text(",
            "    '2026-11-27 12:00:00,000 INFO majestyguard.enrollment: Loading face model\\n'",
            "    + '2026-11-27 12:00:01,000 INFO majestyguard.enrollment: Loading liveness model\\n',",
            "    encoding='utf-8',",
            ")",
            f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
            f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid), encoding='utf-8')",
            "sys.stderr.write('simulated enrollment hang\\n')",
            "sys.stderr.flush()",
            "time.sleep(60)",
        ]),
        encoding="utf-8",
    )

    real_python = Path(sys.executable)
    check = mod.SmokeCheck(
        name="enrollment",
        exe=real_python,
        args=("-u", str(parent_script)),
        timeout_s=1.2,
    )
    failure_report = smoke_state / "smoke-failure.txt"
    messages = []
    results = mod.run_all_checks(
        [check],
        smoke_state_dir=smoke_state,
        failure_report_path=failure_report,
        output=messages.append,
        poll_interval_s=0.05,
    )

    assert len(results) == 1
    res = results[0]
    assert not res.passed
    assert res.timed_out
    report = failure_report.read_text(encoding="utf-8")
    assert "TIMED OUT" in report
    assert "Loading liveness model" in report
    assert "simulated enrollment hang" in report

    # Confirm the child process was also terminated (zero leaked orphans).
    if sys.platform == "win32" and child_pid_file.is_file():
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
        # Give Windows a brief moment to finish reaping handles.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not mod.snapshot_process_tree(child_pid):
                break
            time.sleep(0.05)
        assert mod.snapshot_process_tree(child_pid) == []
        assert len(res.active_tree) >= 2


def test_run_all_checks_stops_on_missing_executable_and_writes_summary(tmp_path):
    mod = _load_module()
    bundle = _make_fake_bundle(tmp_path)
    (bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe").unlink()
    smoke_state = tmp_path / "smoke-state"
    failure_report = smoke_state / "smoke-failure.txt"

    checks = mod.default_checks(bundle)
    results = mod.run_all_checks(
        checks,
        smoke_state_dir=smoke_state,
        failure_report_path=failure_report,
        output=lambda _line: None,
    )

    assert len(results) == 1
    assert results[0].missing_exe
    report = failure_report.read_text(encoding="utf-8")
    assert "MISSING EXECUTABLE" in report
    assert "MajestyGuardDaemon.exe" in report


def test_cli_entrypoint_writes_failure_report_on_broken_bundle(tmp_path):
    bundle = _make_fake_bundle(tmp_path)
    (bundle / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe").unlink()
    smoke_state = tmp_path / "smoke-state"
    failure_report = smoke_state / "smoke-failure.txt"

    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--bundle-root",
            str(bundle),
            "--smoke-state",
            str(smoke_state),
            "--failure-report",
            str(failure_report),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 1
    assert "MISSING EXECUTABLE" in proc.stdout
    assert "MISSING EXECUTABLE" in failure_report.read_text(encoding="utf-8")
