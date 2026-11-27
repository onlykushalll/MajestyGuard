"""Read-only resource sampling for the Control Center."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


def read_verify_timing(path: Path) -> tuple[str, str]:
    """Read the last successful verification in a bounded log tail."""
    try:
        with path.open("rb") as log_file:
            log_file.seek(0, 2)
            log_file.seek(max(0, log_file.tell() - 64 * 1024))
            recent = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        return "--", "--"
    for line in reversed(recent.splitlines()):
        if "[VerifyTiming] outcome=owner_verified " not in line:
            continue
        timings = dict(re.findall(r"\b(decision|camera_ready)_ms=([0-9]{1,8})\b", line))
        if "decision" in timings:
            verify = f"{int(timings['decision']) / 1000:.2f} s"
            camera = f"{int(timings['camera_ready']) / 1000:.2f} s" if "camera_ready" in timings else "--"
            return verify, camera
    return "--", "--"


@dataclass(frozen=True)
class ProcessReading:
    group: str
    pid: int
    ram_bytes: int | None
    cpu_percent: float | None
    uptime_seconds: float | None


class ResourceSampler:
    def __init__(self) -> None:
        self._processes: dict[int, psutil.Process] = {}
        self._cores = psutil.cpu_count() or 1
        self._last_sample_at: float | None = None
        self._last_readings: tuple[ProcessReading, ...] = ()
        self._last_groups: dict[str, list[int]] = {}

    def sample(self, groups: dict[str, list[int]]) -> tuple[ProcessReading, ...]:
        now = time.monotonic()
        if (self._last_sample_at is not None and now - self._last_sample_at < 0.1
                and groups == self._last_groups):
            return self._last_readings
        previous, self._processes = self._processes, {}
        readings = []
        for group, pids in groups.items():
            for pid in dict.fromkeys(pids):
                try:
                    process = previous.get(pid)
                    sampling = process is not None and process.is_running()
                    if not sampling:
                        process = psutil.Process(pid)
                    with process.oneshot():
                        ram = process.memory_info().rss
                        uptime = max(0.0, time.time() - process.create_time())
                    cpu = process.cpu_percent(interval=None) / self._cores
                    readings.append(ProcessReading(group, pid, ram, cpu if sampling else None, uptime))
                    self._processes[pid] = process
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error:
                    readings.append(ProcessReading(group, pid, None, None, None))
        self._last_sample_at = now
        self._last_groups = {group: list(pids) for group, pids in groups.items()}
        self._last_readings = tuple(readings)
        return self._last_readings
