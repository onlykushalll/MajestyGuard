"""Run packaged MajestyGuard smoke and enrollment checks with actionable diagnostics.

Executes each frozen entrypoint under a camera-free, lock-free, isolated
environment, captures stdout/stderr and new ``daemon.log`` entries, detects
blocking Win32 modal error dialogs from ``--windowed`` binaries early, cleans
up the full process tree on timeout or abort, and emits a structured failure
diagnosis instead of an opaque timeout.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable, Iterable, Mapping, Optional, Sequence


DEFAULT_IMPORT_TIMEOUT_S = 45.0
DEFAULT_ENROLLMENT_TIMEOUT_S = 90.0
DEFAULT_POLL_INTERVAL_S = 0.25
MAX_EXCERPT_LINES = 25

_STRIP_ENV_KEYS = (
    "PYINSTALLER_RESET_ENVIRONMENT",
    "_MEIPASS",
    "_MEIPASS2",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
)

_NTSTATUS_NAMES = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
    0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND",
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
}

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    exe: Path
    args: tuple[str, ...]
    timeout_s: float


@dataclass(frozen=True)
class ProcessNode:
    pid: int
    ppid: int
    exe_name: str


@dataclass(frozen=True)
class ModalDialogInfo:
    pid: int
    hwnd: int
    title: str
    body: str


@dataclass
class SmokeCheckResult:
    check: SmokeCheck
    passed: bool
    elapsed_s: float
    returncode: Optional[int] = None
    timed_out: bool = False
    missing_exe: bool = False
    modal_dialog: Optional[ModalDialogInfo] = None
    active_tree: list[ProcessNode] = field(default_factory=list)
    stdout_text: str = ""
    stderr_text: str = ""
    daemon_log_delta: str = ""
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None
    daemon_log_path: Optional[Path] = None
    last_milestone: str = ""
    diagnoses: list[str] = field(default_factory=list)


def default_checks(
    bundle_root: Path,
    *,
    import_timeout_s: float = DEFAULT_IMPORT_TIMEOUT_S,
    enrollment_timeout_s: float = DEFAULT_ENROLLMENT_TIMEOUT_S,
) -> list[SmokeCheck]:
    """Build the ordered smoke/enrollment verification plan for an onedir bundle."""
    bundle_root = Path(bundle_root)
    daemon_exe = bundle_root / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe"
    control_center_exe = bundle_root / "MajestyGuard" / "MajestyGuard.exe"
    overlay_exe = bundle_root / "MajestyGuardUI" / "MajestyGuardUI.exe"
    return [
        SmokeCheck("daemon-imports", daemon_exe, ("--self-test",), float(import_timeout_s)),
        SmokeCheck("enrollment", daemon_exe, ("--enroll", "--self-test"), float(enrollment_timeout_s)),
        SmokeCheck("control-center", control_center_exe, ("--self-test",), float(import_timeout_s)),
        SmokeCheck("overlay-ui", overlay_exe, ("--self-test",), float(import_timeout_s)),
    ]


def build_smoke_env(
    smoke_state_dir: Path,
    base_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return a clean, camera/lock/IPC-safe environment for packaged smoke checks."""
    smoke_state_dir = Path(smoke_state_dir).resolve()
    mpl_dir = smoke_state_dir / "mplconfig"
    smoke_state_dir.mkdir(parents=True, exist_ok=True)
    mpl_dir.mkdir(parents=True, exist_ok=True)

    source = os.environ if base_env is None else base_env
    env = dict(source)
    venv_dir = env.get("VIRTUAL_ENV")
    for key in _STRIP_ENV_KEYS:
        env.pop(key, None)

    if venv_dir and "PATH" in env:
        venv_root = Path(venv_dir).resolve()
        filtered_path: list[str] = []
        for entry in env["PATH"].split(os.pathsep):
            cleaned = entry.strip('"')
            if not cleaned:
                continue
            try:
                if Path(cleaned).resolve().is_relative_to(venv_root):
                    continue
            except OSError:
                pass
            filtered_path.append(entry)
        env["PATH"] = os.pathsep.join(filtered_path)

    env["MG_ENABLE_LOCK"] = "0"
    env["MG_ENABLE_WHCDF_IPC"] = "0"
    env["MG_ENABLE_SERVICE_IPC"] = "0"
    env["LOCALAPPDATA"] = str(smoke_state_dir)
    env["MPLCONFIGDIR"] = str(mpl_dir)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def format_exit_code(returncode: Optional[int]) -> str:
    """Format an exit code with unsigned NTSTATUS hex/name when applicable."""
    if returncode is None:
        return "none"
    if returncode < 0 or returncode >= 0x80000000:
        unsigned = returncode & 0xFFFFFFFF
        label = _NTSTATUS_NAMES.get(unsigned)
        if label:
            return f"{returncode} (0x{unsigned:08X}: {label})"
        return f"{returncode} (0x{unsigned:08X})"
    return str(returncode)


def read_log_delta(log_path: Path, start_offset: int) -> str:
    """Read text appended to ``log_path`` since ``start_offset``."""
    if not log_path.is_file():
        return ""
    try:
        size = log_path.stat().st_size
        offset = 0 if size < start_offset else start_offset
        with log_path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_text_safe(path: Optional[Path]) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail_lines(text: str, limit: int = MAX_EXCERPT_LINES) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= limit:
        return lines
    omitted = len(lines) - limit
    return [f"... ({omitted} earlier lines omitted)"] + lines[-limit:]


def extract_last_milestone(daemon_log_delta: str, stderr_text: str, stdout_text: str) -> str:
    """Return the most informative last phase or error line across captured logs."""
    for source_name, text in (
        ("daemon.log", daemon_log_delta),
        ("stderr", stderr_text),
        ("stdout", stdout_text),
    ):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        for line in reversed(lines):
            if "INFO " in line or "ERROR " in line or "CRITICAL " in line or "WARNING " in line:
                return f"{source_name}: {line}"
        return f"{source_name}: {lines[-1]}"
    return ""


def diagnose_failure(
    *,
    check: SmokeCheck,
    returncode: Optional[int],
    timed_out: bool,
    missing_exe: bool,
    modal_dialog: Optional[ModalDialogInfo],
    active_tree: Sequence[ProcessNode],
    stdout_text: str,
    stderr_text: str,
    daemon_log_delta: str,
) -> list[str]:
    """Produce concise, evidence-backed failure diagnoses and next actions."""
    hints: list[str] = []
    combined = "\n".join(
        part
        for part in (
            modal_dialog.title if modal_dialog else "",
            modal_dialog.body if modal_dialog else "",
            daemon_log_delta,
            stderr_text,
            stdout_text,
        )
        if part
    )

    if missing_exe:
        hints.append(
            f"Executable missing at '{check.exe}'. Ensure PyInstaller built the target without errors."
        )
        return hints

    if modal_dialog is not None:
        detail = modal_dialog.body or modal_dialog.title or "untitled dialog"
        hints.append(
            "A blocking Windows/PyInstaller modal dialog (#32770) appeared instead of a clean exit: "
            f"{detail}"
        )

    mod_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", combined)
    if mod_match:
        missing_mod = mod_match.group(1)
        hints.append(
            f"Missing frozen module '{missing_mod}'. Add '--hidden-import {missing_mod}' "
            "(or required package files) in packaging/build_bundle.ps1."
        )
    elif "ImportError:" in combined:
        hints.append(
            "Frozen Python import failed. Check PyInstaller --hidden-import/--exclude-module "
            "flags in packaging/build_bundle.ps1."
        )

    unsigned = (returncode & 0xFFFFFFFF) if returncode is not None else None
    if (
        unsigned in (0xC0000135, 0xC0000139, 0xC0000142)
        or "DLL load failed" in combined
        or "WinError 126" in combined
        or "WinError 127" in combined
        or "WinError 1114" in combined
    ):
        hints.append(
            "Native DLL or VC++ CRT initialization failed. Verify packaging/install_bundle_crt.ps1 "
            "copied coherent runtime DLLs into the daemon root, _internal, and PyQt6\\Qt6\\bin."
        )
    elif unsigned == 0xC0000005:
        hints.append(
            "Native access violation (0xC0000005). Check VC++ CRT and native wheel alignment "
            "between PyQt6, SciPy, MediaPipe, and ONNX Runtime."
        )

    if "TimeoutExpired" in combined:
        hints.append(
            "A child subprocess timed out inside the launcher (for example MajestyGuard.exe invoking "
            "MajestyGuardDaemon.exe --enroll --self-test); inspect daemon.log phases and timeout budget."
        )

    lower_lines = combined.lower().splitlines()
    onnx_problem = any(
        ("cpuexecutionprovider" in line and any(k in line for k in ("missing", "not found", "unavailable", "failed", "assertionerror")))
        or ("onnx" in line and any(k in line for k in ("error", "failed", "invalid", "missing", "load failed")))
        for line in lower_lines
    )
    if onnx_problem:
        hints.append(
            "ONNX Runtime CPUExecutionProvider or bundled ONNX model initialization failed; check "
            "packaging/verify_model_bundle.py and --collect-binaries onnxruntime."
        )
    elif check.name == "daemon-imports" and returncode == 1 and not combined.strip():
        hints.append(
            "MajestyGuardDaemon.exe --self-test exited 1 with no output; verify onnxruntime "
            "CPUExecutionProvider availability and bundled DLLs."
        )

    if check.name == "enrollment" and "unrecognized arguments: --self-test" in combined:
        hints.append(
            "daemon/enroll_continuous.py in the packaged binary lacks '--self-test' handling "
            "(argparse exited on unrecognized '--self-test'). Add '--self-test' support to "
            "daemon/enroll_continuous.py and rebuild the bundle."
        )

    if timed_out:
        if active_tree:
            tree_summary = ", ".join(f"{node.exe_name}({node.pid})" for node in active_tree)
            hints.append(f"Process tree still active at timeout: {tree_summary}.")
        if not combined.strip() and check.name in ("enrollment", "daemon-imports", "control-center"):
            hints.append(
                "No daemon.log, stdout, or stderr entries were recorded before timeout. The process "
                "likely stalled during PyInstaller unpack, top-level imports before logging setup, "
                "or an early loader wait."
            )
        elif "Phase-2 full-liveness models loaded" in combined:
            hints.append(
                "Enrollment stalled after model load during scipy.signal validation or synthetic "
                "frame analyzer.detect()."
            )
        elif "MiDaS depth model loaded" in combined and "AttentionDetector ready" not in combined:
            hints.append(
                "Enrollment stalled after MiDaS load while initializing MediaPipe FaceMesh / "
                "matplotlib font cache (verify --collect-data mediapipe and MPLCONFIGDIR)."
            )
        elif "Loading face model" in combined and "Loading liveness model" not in combined:
            hints.append(
                "Enrollment stalled while loading InsightFace buffalo_l models (verify "
                "models_insightface payload and CPUExecutionProvider)."
            )
        elif "Loading liveness model" in combined:
            hints.append(
                "Enrollment stalled while initializing liveness models (MiniFASNetV2, MiDaS, or "
                "MediaPipe AttentionDetector)."
            )
        elif not hints:
            hints.append(
                f"Check exceeded {check.timeout_s:.1f}s timeout; review captured logs or raise "
                "-SmokeTimeoutSeconds if cold-start model loading needs more headroom."
            )

    if not hints:
        hints.append(
            "Process exited non-zero without a recognized signature; inspect captured stdout, "
            "stderr, and daemon.log lines below."
        )
    return hints


def format_failure_report(result: SmokeCheckResult) -> str:
    """Render an actionable multiline diagnostic report for a failed/timed-out check."""
    if result.missing_exe:
        status = "MISSING EXECUTABLE"
    elif result.modal_dialog is not None:
        status = "BLOCKED BY DIALOG"
    elif result.timed_out:
        status = "TIMED OUT"
    else:
        status = "FAILED"

    cmd = " ".join([str(result.check.exe), *result.check.args])
    lines = [
        f"Packaged {result.check.name} check {status}: {cmd}",
        (
            f"Elapsed: {result.elapsed_s:.2f}s / Timeout: {result.check.timeout_s:.1f}s | "
            f"Exit code: {format_exit_code(result.returncode)}"
        ),
    ]

    if result.active_tree:
        lines.append(
            "Active process tree: "
            + " -> ".join(f"{node.exe_name} (PID {node.pid}, PPID {node.ppid})" for node in result.active_tree)
        )

    if result.modal_dialog is not None:
        lines.append(
            f"Modal dialog (PID {result.modal_dialog.pid}): title={result.modal_dialog.title!r}"
        )
        if result.modal_dialog.body:
            lines.append(f"Modal dialog text: {result.modal_dialog.body}")

    if result.last_milestone:
        lines.append(f"Last observed milestone: {result.last_milestone}")

    if result.diagnoses:
        lines.append("Actionable diagnosis:")
        for item in result.diagnoses:
            lines.append(f"  - {item}")

    for label, text, path in (
        ("daemon.log (new entries)", result.daemon_log_delta, result.daemon_log_path),
        ("stderr", result.stderr_text, result.stderr_path),
        ("stdout", result.stdout_text, result.stdout_path),
    ):
        excerpt = _tail_lines(text)
        if excerpt:
            loc = f" [{path}]" if path else ""
            lines.append(f"--- {label}{loc} ---")
            lines.extend(excerpt)

    log_refs = [
        f"{name}={path}"
        for name, path in (
            ("stdout", result.stdout_path),
            ("stderr", result.stderr_path),
            ("daemon.log", result.daemon_log_path),
        )
        if path is not None
    ]
    if log_refs:
        lines.append("Logs: " + ", ".join(log_refs))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Windows process-tree & modal-dialog inspection helpers
# ---------------------------------------------------------------------------


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def snapshot_process_tree(root_pid: int) -> list[ProcessNode]:
    """Return ``root_pid`` and its live descendant processes in BFS order."""
    if sys.platform != "win32" or root_pid <= 0:
        return []

    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return []

    by_pid: dict[int, ProcessNode] = {}
    children: dict[int, list[int]] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            node = ProcessNode(
                pid=int(entry.th32ProcessID),
                ppid=int(entry.th32ParentProcessID),
                exe_name=str(entry.szExeFile),
            )
            by_pid[node.pid] = node
            children.setdefault(node.ppid, []).append(node.pid)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)

    if root_pid not in by_pid:
        return []

    ordered: list[ProcessNode] = []
    queue = [root_pid]
    seen: set[int] = set()
    while queue:
        pid = queue.pop(0)
        if pid in seen or pid not in by_pid:
            continue
        seen.add(pid)
        ordered.append(by_pid[pid])
        queue.extend(children.get(pid, ()))
    return ordered


def terminate_process_tree(proc: subprocess.Popen, nodes: Sequence[ProcessNode]) -> None:
    """Terminate the entire process tree rooted at ``proc`` without leaving orphans."""
    if sys.platform == "win32":
        k32 = _kernel32()
        # Re-snapshot to catch any child spawned right before termination.
        live_nodes = snapshot_process_tree(proc.pid) or list(nodes)
        pids_bottom_up = [n.pid for n in reversed(live_nodes) if n.pid != proc.pid]
        for pid in pids_bottom_up:
            handle = k32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
            if handle:
                try:
                    k32.TerminateProcess(handle, 1)
                    k32.WaitForSingleObject(handle, 2000)
                finally:
                    k32.CloseHandle(handle)

    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        pass


def find_blocking_dialog(pids: set[int]) -> Optional[ModalDialogInfo]:
    """Detect a Windows ``#32770`` modal dialog (such as PyInstaller runw error) for ``pids``."""
    if sys.platform != "win32" or not pids:
        return None

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wnd_enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [wnd_enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, wnd_enum_proc, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    def read_text(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(2048)
        length = user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value[:length].strip() if length > 0 else ""

    def read_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value[:length] if length > 0 else ""

    matches: list[ModalDialogInfo] = []

    def on_top_window(hwnd: int, _lparam: int) -> bool:
        owner_pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value not in pids:
            return True
        if read_class(hwnd) != "#32770":
            return True

        title = read_text(hwnd)
        parts: list[str] = []

        def on_child_window(child_hwnd: int, _child_lparam: int) -> bool:
            text = read_text(child_hwnd)
            cls = read_class(child_hwnd)
            if text and cls != "Button":
                if text not in parts:
                    parts.append(text)
            return True

        child_cb = wnd_enum_proc(on_child_window)
        user32.EnumChildWindows(hwnd, child_cb, 0)
        body = " | ".join(parts)
        if title or body:
            matches.append(
                ModalDialogInfo(
                    pid=int(owner_pid.value),
                    hwnd=int(hwnd),
                    title=title,
                    body=body,
                )
            )
            return False
        return True

    top_cb = wnd_enum_proc(on_top_window)
    user32.EnumWindows(top_cb, 0)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_check(
    check: SmokeCheck,
    *,
    smoke_state_dir: Path,
    env: Mapping[str, str],
    step_index: int = 1,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    popen_fn: Callable[..., subprocess.Popen] = subprocess.Popen,
    tree_inspector: Callable[[int], list[ProcessNode]] = snapshot_process_tree,
    dialog_inspector: Callable[[set[int]], Optional[ModalDialogInfo]] = find_blocking_dialog,
    tree_terminator: Callable[[subprocess.Popen, Sequence[ProcessNode]], None] = terminate_process_tree,
) -> SmokeCheckResult:
    """Execute one packaged check with stdout/stderr/log capture and early hang detection."""
    if not check.exe.is_file():
        result = SmokeCheckResult(
            check=check,
            passed=False,
            elapsed_s=0.0,
            missing_exe=True,
        )
        result.diagnoses = diagnose_failure(
            check=check,
            returncode=None,
            timed_out=False,
            missing_exe=True,
            modal_dialog=None,
            active_tree=(),
            stdout_text="",
            stderr_text="",
            daemon_log_delta="",
        )
        return result

    logs_dir = smoke_state_dir / "smoke-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{step_index:02d}-{check.name}.stdout.log"
    stderr_path = logs_dir / f"{step_index:02d}-{check.name}.stderr.log"
    daemon_log_path = smoke_state_dir / "MajestyGuard" / "daemon.log"
    log_offset = daemon_log_path.stat().st_size if daemon_log_path.is_file() else 0

    started = clock()
    timed_out = False
    modal_dialog: Optional[ModalDialogInfo] = None
    active_tree: list[ProcessNode] = []

    popen_kwargs: dict[str, object] = {
        "cwd": str(check.exe.parent),
        "env": dict(env),
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32" and _CREATE_NO_WINDOW:
        popen_kwargs["creationflags"] = _CREATE_NO_WINDOW

    with stdout_path.open("wb") as out_stream, stderr_path.open("wb") as err_stream:
        proc = popen_fn(
            [str(check.exe), *check.args],
            stdout=out_stream,
            stderr=err_stream,
            **popen_kwargs,
        )
        while proc.poll() is None:
            tree = tree_inspector(proc.pid)
            pids = {node.pid for node in tree}
            if proc.pid > 0:
                pids.add(proc.pid)
            modal_dialog = dialog_inspector(pids)
            if modal_dialog is not None:
                active_tree = tree
                tree_terminator(proc, tree)
                break

            elapsed = clock() - started
            if elapsed >= check.timeout_s:
                if proc.poll() is not None:
                    break
                timed_out = True
                active_tree = tree
                tree_terminator(proc, tree)
                break
            sleep(poll_interval_s)

        returncode = proc.poll()

    elapsed_s = max(0.0, clock() - started)
    stdout_text = _read_text_safe(stdout_path)
    stderr_text = _read_text_safe(stderr_path)
    daemon_log_delta = read_log_delta(daemon_log_path, log_offset)
    passed = (returncode == 0) and not timed_out and modal_dialog is None

    result = SmokeCheckResult(
        check=check,
        passed=passed,
        elapsed_s=elapsed_s,
        returncode=returncode,
        timed_out=timed_out,
        modal_dialog=modal_dialog,
        active_tree=active_tree,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        daemon_log_delta=daemon_log_delta,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        daemon_log_path=daemon_log_path if daemon_log_path.is_file() else None,
    )
    if not passed:
        result.last_milestone = extract_last_milestone(daemon_log_delta, stderr_text, stdout_text)
        result.diagnoses = diagnose_failure(
            check=check,
            returncode=returncode,
            timed_out=timed_out,
            missing_exe=False,
            modal_dialog=modal_dialog,
            active_tree=active_tree,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            daemon_log_delta=daemon_log_delta,
        )
    return result


def run_all_checks(
    checks: Iterable[SmokeCheck],
    *,
    smoke_state_dir: Path,
    failure_report_path: Optional[Path] = None,
    base_env: Optional[Mapping[str, str]] = None,
    output: Optional[Callable[[str], None]] = None,
    **run_kwargs,
) -> list[SmokeCheckResult]:
    """Run checks sequentially, stopping on the first failure with an actionable report."""
    check_list = list(checks)
    env = build_smoke_env(smoke_state_dir, base_env=base_env)
    emit = print if output is None else output
    if failure_report_path is not None:
        failure_report_path.unlink(missing_ok=True)

    results: list[SmokeCheckResult] = []
    total = len(check_list)
    for index, check in enumerate(check_list, start=1):
        emit(
            f"[smoke] {index}/{total} {check.name}: {check.exe.name} "
            f"{' '.join(check.args)} (timeout {check.timeout_s:.0f}s)"
        )
        result = run_check(
            check,
            smoke_state_dir=smoke_state_dir,
            env=env,
            step_index=index,
            **run_kwargs,
        )
        results.append(result)
        if result.passed:
            emit(f"[smoke] {index}/{total} {check.name}: PASS ({result.elapsed_s:.2f}s)")
            continue

        report = format_failure_report(result)
        if failure_report_path is not None:
            failure_report_path.parent.mkdir(parents=True, exist_ok=True)
            failure_report_path.write_text(report, encoding="utf-8")
        emit(report)
        break
    return results


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--smoke-state", type=Path, required=True)
    parser.add_argument("--failure-report", type=Path, default=None)
    parser.add_argument("--import-timeout", type=float, default=DEFAULT_IMPORT_TIMEOUT_S)
    parser.add_argument("--enrollment-timeout", type=float, default=DEFAULT_ENROLLMENT_TIMEOUT_S)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    checks = default_checks(
        args.bundle_root,
        import_timeout_s=args.import_timeout,
        enrollment_timeout_s=args.enrollment_timeout,
    )
    results = run_all_checks(
        checks,
        smoke_state_dir=args.smoke_state,
        failure_report_path=args.failure_report,
        output=lambda line: print(line, flush=True),
    )
    return 0 if results and all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
