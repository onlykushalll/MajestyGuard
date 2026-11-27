"""
workflow_test.py - Supervised soft-lock workflow timing test.

This script prepares a supervised daemon-owned UI run. MG_ENABLE_LOCK=0
disables automatic Windows locking by the daemon, not the UI's deliberate
Windows-lock fallback.

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
from pathlib import Path

from idle_monitor import clamp_idle_timeout

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
DAEMON = ROOT / "daemon" / "main.py"


def _env(idle_timeout: int, max_seconds: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "MG_ENABLE_LOCK": "0",
            "MG_ENABLE_WHCDF_IPC": "0",
            "MG_ENABLE_SERVICE_IPC": "0",
            "MG_IDLE_TIMEOUT": str(idle_timeout),
            "MG_PASSIVE_FPS": "0",
            # The daemon must create its own UI process.  The command pipe
            # intentionally rejects sibling processes so a separately-started
            # UI cannot request verification on its behalf.
            "MG_OVERLAY_WATCHDOG": "1",
            "MG_OVERLAY_WATCHDOG_INTERVAL_S": "0.5",
            "MG_MAX_SECONDS": str(max_seconds),
            "MG_LOG_EVERY_N_FRAMES": "5",
            "MG_BURST_FAST_CONFIRM_FRAMES": "3",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _start_daemon(env: dict[str, str]) -> subprocess.Popen:
    daemon = subprocess.Popen(
        [str(PYTHON), str(DAEMON)],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return daemon


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _print_operator_steps(idle_timeout: int, runs: int, max_seconds: int) -> None:
    print("=== MajestyGuard Supervised Workflow Timing ===")
    print("Safety: the overlay captures input; run only while at the PC.")
    print("Automatic daemon Windows locking is disabled. Do not press Tab on the overlay.")
    print("Its manual Windows-lock fallback is independent of MG_ENABLE_LOCK.")
    print(f"Planned cycles (manual): {runs}")
    print(f"Requested idle timeout: {idle_timeout}s (saved settings can override it)")
    print(f"Daemon camera-loop time limit: {max_seconds}s")
    print()
    print("For each run:")
    print("  1. Sit centered, good daylight, face camera.")
    print("  2. Wait until the daemon reports ACTIVE.")
    print("  3. Stop desktop input for the effective idle timeout.")
    print("  4. The daemon-owned overlay should appear as locked_passive / LOCKED.")
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
    idle_timeout = int(clamp_idle_timeout(args.idle_timeout))
    max_seconds = args.max_seconds or min(86400, args.runs * (idle_timeout + 25))
    env = _env(idle_timeout, max_seconds)
    _print_operator_steps(idle_timeout, args.runs, max_seconds)

    if args.dry_run:
        print("Dry run only. Environment:")
        print(json.dumps({k: env[k] for k in sorted(env) if k.startswith("MG_")}, indent=2))
        return 0

    daemon = None
    try:
        daemon = _start_daemon(env)
        print("Daemon started. It will launch its owned UI after SOFT_LOCK.")
        print("Use the visual UI and daemon logs to record T1/T2/T3/T4.")
        print("Stop with Ctrl+C after the requested runs.")
        if daemon.stdout is not None:
            for line in daemon.stdout:
                print(line, end="")
        return daemon.wait()
    except KeyboardInterrupt:
        print("\nStopping supervised workflow test.")
        return 130
    finally:
        _stop_process(daemon)


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervised MajestyGuard soft-lock timing test.")
    parser.add_argument("--idle-timeout", type=int, default=30, help="Idle timeout for test runs, clamped by daemon to 3-1800 seconds.")
    parser.add_argument("--runs", type=int, default=3, help="Number of manually supervised cycles to plan for.")
    parser.add_argument("--max-seconds", type=int, default=0, help="Daemon camera-loop time limit (0 derives it from the planned cycles).")
    parser.add_argument("--dry-run", action="store_true", help="Print instructions/env without starting camera/UI.")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not 0 <= args.max_seconds <= 86400:
        parser.error("--max-seconds must be between 0 and 86400")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
