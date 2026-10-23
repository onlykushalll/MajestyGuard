"""Process supervision for the local MajestyGuard daemon."""
from __future__ import annotations

import os
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from app.config import (
    MAX_IDLE_TIMEOUT_SECONDS,
    MIN_IDLE_TIMEOUT_SECONDS,
    RuntimeSettings,
    load_settings,
    save_settings,
)


class RuntimeStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    DEGRADED = "degraded"


class Supervisor:
    """Own the daemon lifecycle without becoming a biometric decision-maker."""

    def __init__(
        self,
        root: Path,
        python_executable: Path,
        settings_path: Path,
        *,
        local_app_data: Optional[Path] = None,
        daemon_executable: Optional[Path] = None,
    ) -> None:
        self._root = Path(root)
        self._python = Path(python_executable)
        self._settings_path = Path(settings_path)
        self._local_app_data = Path(local_app_data) if local_app_data else Path(
            os.environ.get("LOCALAPPDATA", "C:/tmp")
        )
        self._daemon_executable = Path(daemon_executable) if daemon_executable else None
        self._process: Optional[subprocess.Popen] = None
        self._enrollment_process: Optional[subprocess.Popen] = None
        self._resume_after_enrollment: bool = False

    def status(self) -> RuntimeStatus:
        if self._enrollment_process is not None and self._enrollment_process.poll() is not None:
            self._enrollment_process = None
            if self._resume_after_enrollment and self.enrollment_present():
                self._resume_after_enrollment = False
                self.start()
        if self._process is None:
            return RuntimeStatus.PAUSED if load_settings(self._settings_path).paused else RuntimeStatus.STOPPED
        return RuntimeStatus.RUNNING if self._process.poll() is None else RuntimeStatus.DEGRADED

    def enrollment_present(self) -> bool:
        state_dir = self._local_app_data / "MajestyGuard"
        return (state_dir / "embeddings_v2.npy").is_file() or (state_dir / "biometric.mgd").is_file()

    def start(self) -> bool:
        if self.status() in (RuntimeStatus.STARTING, RuntimeStatus.RUNNING):
            return False

        source_entrypoint = self._root / "daemon" / "main.py"
        daemon_command: list[str]
        packaged_daemon = self._packaged_daemon_executable()
        if packaged_daemon is not None:
            daemon_command = [str(packaged_daemon)]
        elif source_entrypoint.is_file():
            daemon_command = [str(self._python), str(source_entrypoint)]
        else:
            return False

        settings = load_settings(self._settings_path)
        if settings.paused:
            return False

        environment = dict(os.environ)
        environment.update(
            {
                "MG_ENABLE_LOCK": "1" if settings.enable_windows_lock else "0",
                "MG_MAX_SECONDS": "0",
                "MG_OVERLAY_WATCHDOG": "1",
                "MG_IDLE_TIMEOUT": str(settings.idle_timeout_seconds),
                "MG_PASSIVE_FPS": "0",
                "MG_ENABLE_WHCDF_IPC": "0",
                "MG_ENABLE_SERVICE_IPC": "0",
                "MG_ADAFACE_FLIP_FUSION": "1",
                "MG_IDLE_CHECK_MODE": "1",
                "MG_PERSISTENT_IDLE_LOOP": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        packaged_ui = self._packaged_ui_executable()
        if packaged_ui is not None:
            environment["MG_UI_EXECUTABLE"] = str(packaged_ui)
        try:
            self._process = self._spawn(daemon_command, environment)
        except OSError:
            self._process = None
            return False
        return True

    def stop(self, timeout_seconds: float = 5.0) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=max(0.1, float(timeout_seconds)))
        except subprocess.TimeoutExpired:
            process.kill()

    def pause(self) -> None:
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=True,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        self.stop()

    def resume(self) -> bool:
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=False,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        return self.start()

    def set_idle_timeout(self, seconds: int) -> bool:
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            return False
        if not MIN_IDLE_TIMEOUT_SECONDS <= seconds <= MAX_IDLE_TIMEOUT_SECONDS:
            return False
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=seconds,
                paused=settings.paused,
                enable_windows_lock=settings.enable_windows_lock,
            ),
        )
        return True

    def windows_lock_enabled(self) -> bool:
        return load_settings(self._settings_path).enable_windows_lock

    def set_windows_lock_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            return False
        settings = load_settings(self._settings_path)
        save_settings(
            self._settings_path,
            RuntimeSettings(
                idle_timeout_seconds=settings.idle_timeout_seconds,
                paused=settings.paused,
                enable_windows_lock=enabled,
            ),
        )
        if self.status() is RuntimeStatus.RUNNING:
            self.stop()
            self.start()
        return True

    def start_enrollment(self) -> bool:
        """Launch the existing guided enrollment tool at most once at a time."""
        if self._enrollment_process is not None and self._enrollment_process.poll() is None:
            return False

        if self.status() in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING):
            self._resume_after_enrollment = True
            self.stop()

        packaged_daemon = self._packaged_daemon_executable()
        if packaged_daemon is not None and packaged_daemon.is_file():
            cmd = [str(packaged_daemon), "--enroll"]
        else:
            entrypoint = self._root / "daemon" / "enroll_v2.py"
            if not entrypoint.is_file():
                return False
            cmd = [str(self._python), str(entrypoint)]

        environment = dict(os.environ)
        environment.update(
            {
                "MG_ENABLE_LOCK": "0",
                "MG_ENABLE_WHCDF_IPC": "0",
                "MG_ENABLE_SERVICE_IPC": "0",
                "PYTHONUNBUFFERED": "1",
            }
        )
        try:
            self._enrollment_process = self._spawn_enrollment(
                cmd,
                environment,
            )
        except OSError:
            self._enrollment_process = None
            return False
        return True

    def open_log_directory(self) -> Path:
        """Return the local diagnostic directory without exposing biometric data."""
        path = self._local_app_data / "MajestyGuard"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _spawn(self, command: Sequence[str], environment: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            list(command),
            cwd=str(self._root),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _spawn_enrollment(self, command: Sequence[str], environment: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            list(command),
            cwd=str(self._root),
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )

    def _packaged_daemon_executable(self) -> Optional[Path]:
        candidates = (
            self._daemon_executable,
            self._root / "MajestyGuardDaemon.exe",
            self._root / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
        )
        return next((candidate for candidate in candidates if candidate is not None and candidate.is_file()), None)

    def _packaged_ui_executable(self) -> Optional[Path]:
        candidates = (
            self._root / "MajestyGuardUI.exe",
            self._root / "MajestyGuardUI" / "MajestyGuardUI.exe",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)
