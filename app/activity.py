"""Read-only views of what the daemon wrote: the audit trail and camera health.

Pure Python (no Qt) so it is cheap to test. The audit log carries no biometric
data by design (event names and short non-biometric details only), so showing it
in the Control Center leaks nothing the lock screen would not.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TAIL_BYTES = 96 * 1024

# event -> (headline, tone). Tone drives the dot colour: ok | info | warn | bad.
_EVENTS = {
    "daemon_start": ("Protection started", "ok"),
    "unlock_face": ("Unlocked with your face", "ok"),
    "soft_lock": ("Screen locked", "info"),
    "verify_requested": ("Face check requested", "info"),
    "verify_failed": ("Face not recognized", "warn"),
    "camera_fault": ("Camera problem", "warn"),
    "windows_lock": ("Handed over to the Windows lock", "info"),
    "enroll_completed": ("Face profile saved", "ok"),
    "enroll_rejected": ("Re-enrollment refused", "bad"),
    "gallery_restored": ("Face profile restored from backup", "warn"),
}
_HIDDEN = {"state"}  # one line per internal transition is noise, not history

_REASONS = {
    "idle": "no activity",
    "manual": "locked on request",
    "away": "you stepped away",
    "social": "someone else in view",
}
_FAULTS = {
    "black_frames": "lens covered or shutter closed",
    "frozen_frames": "camera stopped responding",
    "privacy_blocked": "blocked by Windows privacy settings",
    "open_failed": "camera could not be opened",
    "no_frames": "camera sent no images",
}


@dataclass(frozen=True)
class Activity:
    ts: float
    title: str
    detail: str
    tone: str


def _digest(prev: str, body: dict) -> str:
    # Identical to daemon/audit_log._digest; tests assert the two stay in step.
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()


def verify_integrity(path: Path) -> tuple[bool, int, str]:
    """(ok, records, message). A missing file is simply an empty, intact log."""
    path = Path(path)
    if not path.is_file():
        return True, 0, "No activity recorded yet"
    prev: Optional[str] = None
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                    claimed_hash = rec.pop("hash")
                    claimed_prev = rec.pop("prev")
                except Exception:
                    return False, count, f"Line {number} is unreadable"
                if prev is not None and claimed_prev != prev:
                    return False, count, f"Line {number}: link to the previous record is broken"
                if _digest(claimed_prev, rec) != claimed_hash:
                    return False, count, f"Line {number}: a record was altered"
                prev = claimed_hash
                count += 1
    except OSError as exc:
        return False, count, f"Cannot read the log ({exc.__class__.__name__})"
    return True, count, "History is intact"


def _describe(event: str, detail: dict) -> tuple[str, str, str]:
    title, tone = _EVENTS.get(event, (event.replace("_", " ").capitalize(), "info"))
    note = ""
    if event == "soft_lock":
        note = _REASONS.get(str(detail.get("reason", "")), str(detail.get("reason", "")))
    elif event == "verify_failed":
        streak = detail.get("streak")
        cooldown = detail.get("cooldown_s")
        parts = []
        if isinstance(streak, int) and streak > 1:
            parts.append(f"{streak} in a row")
        if isinstance(cooldown, (int, float)) and cooldown:
            parts.append(f"retry in {int(cooldown)} s")
        note = ", ".join(parts)
        tone = "bad" if isinstance(streak, int) and streak >= 4 else tone
    elif event == "camera_fault":
        note = _FAULTS.get(str(detail.get("fault", "")), str(detail.get("fault", "")).replace("_", " "))
    elif event == "enroll_rejected":
        note = "the new face did not match the enrolled one"
    elif event == "gallery_restored":
        note = "the live profile was unreadable"
    elif event == "verify_requested":
        note = {"overlay_key": "SPACE on the lock screen"}.get(str(detail.get("by", "")), "")
    return title, note, tone


def read_enroll_result(state_dir: Path, max_age_s: float = 900.0) -> "str | None":
    """One line about the last enrollment attempt (written by the enrollment tool), or None.

    Reads enroll_result.json, which never holds biometric data.
    """
    try:
        data = json.loads((Path(state_dir) / "enroll_result.json").read_text(encoding="utf-8"))
        if time.time() - float(data["ts"]) > max_age_s:
            return None
        outcome = str(data.get("outcome", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {
        "ok": "Last enrollment: saved.",
        "rejected_different_person": "Last enrollment: not saved, the new face did not match your current profile. "
                                     "If your look changed a lot, use Start over.",
        "failed": "Last enrollment: failed. Your previous profile was kept.",
    }.get(outcome)


def read_recent(path: Path, limit: int = 40) -> list[Activity]:
    """Newest first. Reads only the file's tail so a large log costs nothing."""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[Activity] = []
    for line in reversed(raw.splitlines()):
        try:
            rec = json.loads(line)
            event = str(rec["event"])
            ts = float(rec["ts"])
        except Exception:
            continue          # also drops the partial first line of a tail read
        if event in _HIDDEN:
            continue
        detail = rec.get("detail") if isinstance(rec.get("detail"), dict) else {}
        title, note, tone = _describe(event, detail)
        out.append(Activity(ts, title, note, tone))
        if len(out) >= limit:
            break
    return out


def ago(ts: float, now: Optional[float] = None) -> str:
    seconds = max(0.0, (time.time() if now is None else now) - ts)
    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "1 min ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} h ago"
    return time.strftime("%d %b, %H:%M", time.localtime(ts))


@dataclass(frozen=True)
class CameraInfo:
    status: str          # healthy | degraded | recovering | failed
    headline: str
    hint: str
    backend: str
    recoveries: int
    open_failures: int
    next_retry_s: float
    events: tuple
    age_s: float


def read_camera_health(path: Path, now: Optional[float] = None) -> Optional[CameraInfo]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        updated = float(data.get("updated_at", 0.0))
        return CameraInfo(
            status=str(data.get("status", "healthy")),
            headline=str(data.get("headline", "")),
            hint=str(data.get("hint", "")),
            backend=str(data.get("last_backend") or "-"),
            recoveries=int(data.get("recoveries", 0)),
            open_failures=int(data.get("open_failures", 0)),
            next_retry_s=float(data.get("next_retry_in_s", 0.0)),
            events=tuple(str(e) for e in (data.get("events") or [])[-6:]),
            age_s=max(0.0, (time.time() if now is None else now) - updated) if updated else 1e9,
        )
    except (OSError, ValueError, TypeError):
        return None
