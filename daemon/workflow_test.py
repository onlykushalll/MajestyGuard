"""
workflow_test.py - Supervised soft-lock workflow timing test.

This script prepares a bounded camera/UI run and prints exact instructions for
the human operator. It keeps MG_ENABLE_LOCK=0 so no Windows hard-lock happens
during timing validation.

Measures:
  T1: ACTIVE -> INACTIVITY_LOCK / locked_passive
  T2: Space press -> VERIFYING
  T3: VERIFYING -> ACTIVE
  T4: total unlock latency
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
DAEMON = ROOT / "daemon" / "main.py"
UI = ROOT / "ui" / "main.py"


def _env(idle_timeout: int, max_seconds: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "MG_ENABLE_LOCK": "0",
            "MG_ENABLE_WHCDF_IPC": "0",
            "MG_ENABLE_SERVICE_IPC": "0",
            "MG_IDLE_TIMEOUT": str(idle_timeout),
            "MG_PASSIVE_FPS": "0",
            "MG_MAX_SECONDS": str(max_seconds),
            "MG_LOG_EVERY_N_FRAMES": "5",
            "MG_BURST_FAST_CONFIRM_FRAMES": "3",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _start_processes(env: dict[str, str]) -> tuple[subprocess.Popen, subprocess.Popen]:
    daemon = subprocess.Popen(
        [str(PYTHON), str(DAEMON)],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.0)
    ui = subprocess.Popen(
        [str(PYTHON), str(UI)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return daemon, ui


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _print_operator_steps(idle_timeout: int, runs: int) -> None:
    print("=== MajestyGuard Supervised Workflow Timing ===")
    print("Safety: MG_ENABLE_LOCK=0, no Windows hard-lock.")
    print(f"Runs: {runs}")
    print(f"Idle timeout: {idle_timeout}s")
    print()
    print("For each run:")
    print("  1. Sit centered, good daylight, face camera.")
    print("  2. Wait until Dynamic Island says Verified/ACTIVE.")
    print(f"  3. Stop touching laptop for {idle_timeout}s.")
    print("  4. Overlay should appear as locked_passive / LOCKED.")
    print("  5. Press Space once.")
    print("  6. Watch for verifying_lock / VERIFYING, then ACTIVE.")
    print()
    print("Targets:")
    print("  T1 ACTIVE -> locked_passive: within +0.20s of idle timeout.")
    print("  T2 Space -> VERIFYING: < 0.10s.")
    print("  T3 VERIFYING -> ACTIVE: < 1.50s for clean frontal face.")
    print("  T4 total unlock latency: < 2.00s.")
    print()


def run(args: argparse.Namespace) -> int:
    max_seconds = max(args.max_seconds, args.runs * (args.idle_timeout + 25))
    env = _env(args.idle_timeout, max_seconds)
    _print_operator_steps(args.idle_timeout, args.runs)

    if args.dry_run:
        print("Dry run only. Environment:")
        print(json.dumps({k: env[k] for k in sorted(env) if k.startswith("MG_")}, indent=2))
        return 0

    daemon = ui = None
    try:
        daemon, ui = _start_processes(env)
        print("Daemon/UI started. Use the visual UI and daemon logs to record T1/T2/T3/T4.")
        print("Stop with Ctrl+C after the requested runs.")
        while daemon.poll() is None:
            line = daemon.stdout.readline() if daemon.stdout is not None else ""
            if not line:
                time.sleep(0.1)
                continue
            print(line, end="")
    except KeyboardInterrupt:
        print("\nStopping supervised workflow test.")
    finally:
        _stop_process(ui)
        _stop_process(daemon)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervised MajestyGuard soft-lock timing test.")
    parser.add_argument("--idle-timeout", type=int, default=30, help="Idle timeout for test runs, clamped by daemon to 30-600 seconds.")
    parser.add_argument("--runs", type=int, default=3, help="Number of supervised runs to perform.")
    parser.add_argument("--max-seconds", type=int, default=0, help="Hard bound for daemon lifetime.")
    parser.add_argument("--dry-run", action="store_true", help="Print instructions/env without starting camera/UI.")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
