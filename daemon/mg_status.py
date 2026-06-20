"""
MajestyGuard user-space status report.

This command is safe to run any time: it does not open the camera, lock the
workstation, start IPC, or mutate Task Scheduler state.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping


TASK_NAME = "MajestyGuard_UserDaemon"

_SUPPORTS_COLOR = (
    hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
)

# ANSI escape helpers
_GREEN = "\033[32m" if _SUPPORTS_COLOR else ""
_RED = "\033[31m" if _SUPPORTS_COLOR else ""
_YELLOW = "\033[33m" if _SUPPORTS_COLOR else ""
_CYAN = "\033[36m" if _SUPPORTS_COLOR else ""
_DIM = "\033[2m" if _SUPPORTS_COLOR else ""
_BOLD = "\033[1m" if _SUPPORTS_COLOR else ""
_RESET = "\033[0m" if _SUPPORTS_COLOR else ""


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _err(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


def _cyan(text: str) -> str:
    return f"{_CYAN}{text}{_RESET}"


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


def _find_daemon_pid(lines: list[str]) -> int | None:
    for line in lines:
        low = line.lower()
        if "daemon" in low and "main.py" in low:
            for token in line.split():
                if token.strip().isdigit():
                    return int(token.strip())
    return None


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


def _read_lock_state(state_dir: Path) -> str:
    lock_file = state_dir / "lock_state.txt"
    if lock_file.exists():
        return lock_file.read_text(encoding="utf-8").strip()
    return "UNKNOWN"


def _read_cold_start(state_dir: Path) -> str | None:
    pid_file = state_dir / "daemon.pid"
    if not pid_file.exists():
        return None
    log_path = state_dir / "daemon.log"
    if not log_path.exists():
        return None
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        for line in reversed(tail.splitlines()):
            if "Cold start complete:" in line:
                idx = line.index("Cold start complete:")
                return line[idx:]
    except Exception:
        pass
    return None


def collect_status(
    *,
    env: Mapping[str, str] | None = None,
    process_lines: Iterable[str] | None = None,
) -> dict[str, object]:
    env = os.environ if env is None else env
    lines = list(_default_process_lines() if process_lines is None else process_lines)
    daemon_pid = _find_daemon_pid(lines)
    daemon_running = daemon_pid is not None
    local_app_data = Path(env.get("LOCALAPPDATA", env.get("ProgramData", r"C:\ProgramData"))) / "MajestyGuard"
    log_path = local_app_data / "daemon.log"
    embeddings_path = local_app_data / "embeddings_v2.npy"
    lock_state = _read_lock_state(local_app_data)
    cold_start = _read_cold_start(local_app_data)
    return {
        "daemon_running": daemon_running,
        "daemon_pid": daemon_pid,
        "lock_state": lock_state,
        "cold_start": cold_start,
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

    daemon_running = status["daemon_running"]
    daemon_pid = status.get("daemon_pid")
    lock_state = status.get("lock_state", "UNKNOWN")
    cold_start = status.get("cold_start")

    # Daemon status line
    if daemon_running:
        daemon_str = _ok(f"running (PID {daemon_pid})")
    else:
        daemon_str = _err("not detected")

    # Lock state
    if lock_state == "LOCKED":
        lock_str = _warn("LOCKED")
    elif lock_state == "UNLOCKED":
        lock_str = _ok("UNLOCKED")
    else:
        lock_str = _dim("unknown")

    # Task state
    task_state = str(status["task_state"])
    if "not installed" in task_state:
        task_str = _err(task_state)
    else:
        task_str = _ok(task_state)

    # SAC
    sac = str(status["smart_app_control"])
    if sac == "on":
        sac_str = _warn("ON (may block unsigned executables)")
    elif sac == "evaluation":
        sac_str = _warn("evaluation")
    elif sac == "off":
        sac_str = _ok("off")
    else:
        sac_str = _dim(sac)

    # File existence
    def _file_line(label: str, path: str, exists: bool) -> str:
        tag = _ok("exists") if exists else _err("missing")
        return f"  {label}: {_dim(path)} [{tag}]"

    # Env vars
    def _env_line(key: str, val: str) -> str:
        if key == "MG_ENABLE_LOCK":
            color = _ok(val) if val == "1" else _warn(val)
        elif val == "1":
            color = _ok(val)
        else:
            color = _dim(val)
        return f"  {key}={color}"

    lines = [
        "",
        _bold("MajestyGuard Status"),
        f"{'─' * 40}",
        "",
        f"  Daemon:     {daemon_str}",
        f"  Lock state: {lock_str}",
        f"  Startup:    {task_str}",
        f"  SAC:        {sac_str}",
    ]

    if cold_start:
        lines.append(f"  {_cyan(cold_start)}")

    lines += [
        "",
        _bold("Files"),
        _file_line("Log", str(status["log_path"]), bool(status["log_exists"])),
        _file_line("Embeddings", str(status["embeddings_path"]), bool(status["embeddings_exist"])),
        "",
        _bold("Environment"),
    ]

    for key in ("MG_ENABLE_LOCK", "MG_ENABLE_WHCDF_IPC", "MG_ENABLE_SERVICE_IPC", "MG_ADAFACE_FLIP_FUSION"):
        lines.append(_env_line(key, str(env[key])))

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    print(format_status(collect_status()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
