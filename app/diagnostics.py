"""Read-only resource sampling for the Control Center."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class TimingSnapshot:
    model_ms: int | None = None
    warmup_ms: int | None = None
    startup_ms: int | None = None
    camera_ms: int | None = None
    first_result_ms: int | None = None
    decision_ms: int | None = None


def format_ms(value: int | None) -> str:
    return "--" if value is None else f"{value / 1000:.2f} s"


def read_timing_snapshot(path: Path) -> TimingSnapshot:
    """Read complete startup and successful verification records from one bounded tail."""
    try:
        with path.open("rb") as log_file:
            log_file.seek(0, 2)
            start = max(0, log_file.tell() - 64 * 1024)
            log_file.seek(start)
            recent = log_file.read(64 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return TimingSnapshot()
    startup = verification = ()
    lines = recent.split("\n")
    for line in reversed(lines[bool(start):-1]):
        if not startup:
            match = re.search(r"Cold start complete: model=(\d{1,8})ms warmup=(\d{1,8})ms camera=(\d{1,8})ms total=(\d{1,8})ms\s*$", line)
            if match:
                model, warmup, camera, total = map(int, match.groups())
                if max(model, warmup, camera) <= total:
                    startup = (model, warmup, total)
        if not verification and "[VerifyTiming] outcome=owner_verified " in line:
            fields = {key: int(value) for key, value in
                      re.findall(r"\b(decision|camera_ready|first_result)_ms=([0-9]{1,8})(?=\s|$)", line)}
            decision = fields.get("decision")
            camera, first = fields.get("camera_ready"), fields.get("first_result")
            phases = [v for v in (camera, first, decision) if v is not None]
            if decision is not None and phases == sorted(phases):
                verification = (camera, first, decision)
        if startup and verification:
            break
    return TimingSnapshot(*(startup or (None,)*3), *(verification or (None,)*3))


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
