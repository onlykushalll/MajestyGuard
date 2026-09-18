"""Process supervision for the local MajestyGuard daemon."""
from __future__ import annotations

import os
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from app.config import load_settings


class RuntimeStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
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
    ) -> None:
        self._root = Path(root)
        self._python = Path(python_executable)
        self._settings_path = Path(settings_path)
        self._local_app_data = Path(local_app_data) if local_app_data else Path(
            os.environ.get("LOCALAPPDATA", "C:/tmp")
        )
        self._process: Optional[subprocess.Popen] = None
        self._enrollment_process: Optional[subprocess.Popen] = None

    def status(self) -> RuntimeStatus:
        if self._process is None:
            return RuntimeStatus.STOPPED
        return RuntimeStatus.RUNNING if self._process.poll() is None else RuntimeStatus.DEGRADED

    def enrollment_present(self) -> bool:
        state_dir = self._local_app_data / "MajestyGuard"
        return (state_dir / "embeddings_v2.npy").is_file() or (state_dir / "biometric.mgd").is_file()

    def start(self) -> bool:
        if self.status() in (RuntimeStatus.STARTING, RuntimeStatus.RUNNING):
            return False

        entrypoint = self._root / "daemon" / "main.py"
        if not entrypoint.is_file():
            return False

        settings = load_settings(self._settings_path)
        if settings.paused:
            return False

        environment = dict(os.environ)
        environment.update(
            {
                "MG_ENABLE_LOCK": "0",
                "MG_MAX_SECONDS": "0",
                "MG_OVERLAY_WATCHDOG": "1",
                "MG_IDLE_TIMEOUT": str(settings.idle_timeout_seconds),
                "MG_PASSIVE_FPS": "0",
                "MG_ENABLE_WHCDF_IPC": "0",
                "MG_ENABLE_SERVICE_IPC": "0",
                "MG_ADAFACE_FLIP_FUSION": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        try:
            self._process = self._spawn([str(self._python), str(entrypoint)], environment)
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

    def _spawn(self, command: Sequence[str], environment: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(
            list(command),
            cwd=str(self._root),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
