"""
MajestyGuard user-space status report.

This command is safe to run any time: it does not open the camera, lock the
workstation, start IPC, or mutate Task Scheduler state.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, Mapping


TASK_NAME = "MajestyGuard_UserDaemon"


def _default_process_lines() -> list[str]:
    commands = [
        ["wmic", "process", "get", "ProcessId,Name,CommandLine"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,Name,CommandLine | "
            "Format-Table -AutoSize | Out-String -Width 4096",
        ],
    ]
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
        except Exception:
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.splitlines()
    return []


def _task_state() -> str:
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except Exception as exc:
        return f"unknown ({exc})"
    if proc.returncode != 0:
        startup = _startup_fallback_path()
        if startup.exists():
            return f"installed (Startup folder fallback: {startup})"
        return "not installed"
    for line in proc.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip() or "installed"
    return "installed"


def _startup_fallback_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / f"{TASK_NAME}.cmd"
        )
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{TASK_NAME}.cmd"


def _smart_app_control_status() -> str:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$paths = @("
        "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy',"
        "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CI\\Protected'"
        ");"
        "foreach ($p in $paths) {"
        "  if (Test-Path $p) {"
        "    Get-ItemProperty $p | Format-List | Out-String -Width 4096"
        "  }"
        "}",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=5, shell=False)
    except Exception as exc:
        return f"unknown ({exc})"
    text = proc.stdout.lower()
    if "verifiedandreputablepolicystate" in text and "2" in text:
        return "on"
    if "verifiedandreputablepolicystate" in text and "1" in text:
        return "evaluation"
    if "verifiedandreputablepolicystate" in text and "0" in text:
        return "off"
    return "unknown"


def collect_status(
    *,
    env: Mapping[str, str] | None = None,
    process_lines: Iterable[str] | None = None,
) -> dict[str, object]:
    env = os.environ if env is None else env
    lines = list(_default_process_lines() if process_lines is None else process_lines)
    daemon_running = any(
        "daemon" in line.lower() and "main.py" in line.lower()
        for line in lines
    )
    local_app_data = Path(env.get("LOCALAPPDATA", env.get("ProgramData", r"C:\ProgramData"))) / "MajestyGuard"
    log_path = local_app_data / "daemon.log"
    embeddings_path = local_app_data / "embeddings_v2.npy"
    return {
        "daemon_running": daemon_running,
        "task_state": _task_state(),
        "smart_app_control": _smart_app_control_status(),
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "embeddings_path": str(embeddings_path),
        "embeddings_exist": embeddings_path.exists(),
        "env": {
            "MG_ENABLE_LOCK": env.get("MG_ENABLE_LOCK", "0"),
            "MG_ENABLE_WHCDF_IPC": env.get("MG_ENABLE_WHCDF_IPC", "0"),
            "MG_ENABLE_SERVICE_IPC": env.get("MG_ENABLE_SERVICE_IPC", "0"),
            "MG_ADAFACE_FLIP_FUSION": env.get("MG_ADAFACE_FLIP_FUSION", "1"),
        },
    }


def format_status(status: Mapping[str, object]) -> str:
    env = status["env"]
    assert isinstance(env, Mapping)
    lines = [
        "MajestyGuard status",
        f"Daemon: {'running' if status['daemon_running'] else 'not detected'}",
        f"Startup task: {status['task_state']}",
        f"Smart App Control: {status['smart_app_control']}",
        f"Log: {status['log_path']} (exists={status['log_exists']})",
        f"Embeddings: {status['embeddings_path']} (exists={status['embeddings_exist']})",
        f"MG_ENABLE_LOCK: {env['MG_ENABLE_LOCK']}",
        f"MG_ENABLE_WHCDF_IPC: {env['MG_ENABLE_WHCDF_IPC']}",
        f"MG_ENABLE_SERVICE_IPC: {env['MG_ENABLE_SERVICE_IPC']}",
        f"MG_ADAFACE_FLIP_FUSION: {env['MG_ADAFACE_FLIP_FUSION']}",
    ]
    return "\n".join(lines)


def main() -> int:
    print(format_status(collect_status()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
