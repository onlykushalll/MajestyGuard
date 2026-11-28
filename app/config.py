"""Validated, local-only Control Center settings."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_IDLE_TIMEOUT_SECONDS = 60
MIN_IDLE_TIMEOUT_SECONDS = 15
MAX_IDLE_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class RuntimeSettings:
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    paused: bool = False
    enable_windows_lock: bool = False
    stop_media_on_lock: bool = True


def _validated_settings(data: object) -> RuntimeSettings:
    if not isinstance(data, dict):
        return RuntimeSettings()

    idle_timeout = data.get("idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT_SECONDS)
    if not isinstance(idle_timeout, int) or isinstance(idle_timeout, bool):
        idle_timeout = DEFAULT_IDLE_TIMEOUT_SECONDS
    elif not MIN_IDLE_TIMEOUT_SECONDS <= idle_timeout <= MAX_IDLE_TIMEOUT_SECONDS:
        idle_timeout = DEFAULT_IDLE_TIMEOUT_SECONDS

    paused = data.get("paused", False)
    if not isinstance(paused, bool):
        paused = False

    enable_windows_lock = data.get("enable_windows_lock", False)
    if not isinstance(enable_windows_lock, bool):
        enable_windows_lock = False
    stop_media = data.get("stop_media_on_lock", True)
    if not isinstance(stop_media, bool):
        stop_media = True

    return RuntimeSettings(
        idle_timeout_seconds=idle_timeout,
        paused=paused,
        enable_windows_lock=enable_windows_lock,
        stop_media_on_lock=stop_media,
    )


def load_settings(path: Path) -> RuntimeSettings:
    """Load valid settings, falling back to safe defaults for bad local data."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return RuntimeSettings()
    return _validated_settings(data)


def save_settings(path: Path, settings: RuntimeSettings) -> None:
    """Atomically persist settings after applying the same validation as reads."""
    normalized = _validated_settings(asdict(settings))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(asdict(normalized), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
