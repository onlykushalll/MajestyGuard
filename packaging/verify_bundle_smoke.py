"""Run packaged self-tests with isolated data and bounded diagnostics."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time

import psutil


def smoke_env(state, parent=None):
    inherited = os.environ if parent is None else parent
    env = {key: value for key, value in inherited.items()
           if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
           and not key.upper().startswith(("_MEIPASS", "_PYI_", "MG_"))}
    env.update(LOCALAPPDATA=str(state), MPLCONFIGDIR=str(state / "matplotlib"),
               MG_ENABLE_LOCK="0", MG_ENABLE_WHCDF_IPC="0", MG_ENABLE_SERVICE_IPC="0",
               MG_AUDIT_LOG="0", PYINSTALLER_RESET_ENVIRONMENT="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    return env


def _tail(path):
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 8192))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def run_check(label, command, state, env, timeout):
    output_path = state / (label + ".log")
    with output_path.open("wb") as output:
        try:
            process = subprocess.Popen([str(arg) for arg in command], cwd=state,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            return f"could not start: {exc}"
        observed = set()
        deadline = time.monotonic() + timeout
        result = None
        root = None
        try:
            try:
                root = psutil.Process(process.pid)
            except psutil.Error:
                pass
            while process.poll() is None:
                if root is not None and root.is_running():
                    try:
                        observed.update(root.children(recursive=True))
                    except psutil.Error:
                        pass
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result = f"timed out after {timeout:g} s"
                    break
                try:
                    process.wait(timeout=min(remaining, .25))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for child in observed:
                try:
                    # psutil rechecks process identity (including creation time).
                    if child.is_running():
                        child.kill()
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error as exc:
                    result = f"could not clean up an owned child: {exc}"
            if observed:
                _, alive = psutil.wait_procs(observed, timeout=3)
                if alive:
                    result = "child process did not exit"
    if result:
        return result
    if process.returncode:
        return f"exit {process.returncode} (0x{process.returncode & 0xffffffff:08X})"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--smoke-state", type=Path, required=True)
    parser.add_argument("--enrollment-timeout", type=int, default=90, choices=range(5, 601),
                        metavar="5..600")
    args = parser.parse_args(argv)
    base = args.bundle_root.resolve()
    args.smoke_state.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix="check-", dir=args.smoke_state.resolve()))
    env = smoke_env(state)
    daemon = base / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe"
    center = base / "MajestyGuard" / "MajestyGuard.exe"
    for label, command, timeout in (
        ("daemon-imports", [daemon, "--self-test"], 45),
        ("enrollment", [daemon, "--enroll", "--self-test"], args.enrollment_timeout),
        ("control-center", [center, "--self-test"], 90),
        ("studio-render", [center, "--ui-self-test"], 25),
        ("overlay", [base / "MajestyGuardUI" / "MajestyGuardUI.exe", "--self-test"], 45),
    ):
        failure = run_check(label, command, state, env, timeout)
        if failure:
            report = [f"{label}: {failure}", f"Logs: {state}"]
            for path in (state / f"{label}.log", state / "MajestyGuard" / "daemon.log"):
                if tail := _tail(path):
                    report.extend((f"--- {path.name} ---", tail))
            message = "\n".join(report)
            (state / "smoke-failure.txt").write_text(message + "\n", encoding="utf-8")
            print(message, flush=True)
            return 1
        print(f"{label}: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
