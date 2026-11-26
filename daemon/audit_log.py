"""Tamper-evident audit trail for lock / unlock / verification events.

Each line is a JSON object chained to the previous one by SHA-256, so editing,
deleting or reordering any earlier line breaks every hash after it and
``verify_chain`` reports where. It is evidence, not prevention, and only against
casual edits: the chain is unkeyed, so a process running as the same user can recompute
every hash after an edit, and dropping the newest lines leaves a valid chain. Treat it
as a convenience for spotting accidents, not as proof against a determined local attacker.

Privacy: only event names and short non-biometric details are recorded.
Never scores, embeddings, frames or anything that identifies a face.
It never raises into the daemon: a logging failure must not affect locking.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
_MAX_BYTES = 1_000_000
_MAX_DETAIL_CHARS = 120


def _digest(prev: str, body: dict) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()


def _clean(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_DETAIL_CHARS]


class AuditLog:
    def __init__(self, path: Path, *, max_bytes: int = _MAX_BYTES, enabled: bool = True) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.enabled = enabled
        self._lock = threading.Lock()
        self._prev: str | None = None
        self._seq = 0

    # ------------------------------------------------------------------ write
    def record(self, event: str, **detail: Any) -> bool:
        if not self.enabled:
            return False
        try:
            with self._lock:
                if self._prev is None:
                    self._prev, self._seq = self._load_tail()
                body = {
                    "seq": self._seq + 1,
                    "ts": round(time.time(), 3),
                    "event": str(event)[:48],
                    "detail": {str(k)[:32]: _clean(v) for k, v in detail.items()},
                }
                digest = _digest(self._prev, body)
                line = json.dumps({**body, "prev": self._prev, "hash": digest},
                                  sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
                self._prev, self._seq = digest, body["seq"]
            return True
        except Exception:
            return False

    def _load_tail(self) -> tuple[str, int]:
        """Resume the chain from the last well-formed line of the live file."""
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 4096))
                lines = [ln for ln in handle.read().decode("utf-8", "replace").splitlines() if ln.strip()]
            for raw in reversed(lines):
                try:
                    rec = json.loads(raw)
                    return str(rec["hash"]), int(rec["seq"])
                except Exception:
                    continue
        except OSError:
            pass
        return GENESIS, 0

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                backup = self.path.with_suffix(self.path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                os.replace(self.path, backup)
                # The new file's first record links to the rotated file's last
                # hash (self._prev), so the chain continues across the rotation.
        except OSError:
            pass


def verify_chain(path: Path, *, expected_first_prev: str | None = None) -> tuple[bool, int, str]:
    """Check a log file. Returns ``(ok, records_checked, message)``.

    ``expected_first_prev`` pins the first record's link; pass None for a
    rotated file, whose first record legitimately links to a file that moved.
    """
    prev: str | None = None
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
                    return False, count, f"line {number}: unreadable record"
                if prev is None:
                    if expected_first_prev is not None and claimed_prev != expected_first_prev:
                        return False, count, f"line {number}: chain does not start where expected"
                elif claimed_prev != prev:
                    return False, count, f"line {number}: link to the previous record is broken"
                if _digest(claimed_prev, rec) != claimed_hash:
                    return False, count, f"line {number}: record was altered"
                prev = claimed_hash
                count += 1
    except OSError as exc:
        return False, count, f"cannot read log: {exc}"
    return True, count, "chain intact"


# ---------------------------------------------------------------- module API
_default: AuditLog | None = None
_default_lock = threading.Lock()


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(base) / "MajestyGuard" / "audit.log"


def get_log() -> AuditLog:
    global _default
    with _default_lock:
        if _default is None:
            enabled = os.environ.get("MG_AUDIT_LOG", "1").strip().lower() not in {"0", "false", "off", "no"}
            _default = AuditLog(default_path(), enabled=enabled)
        return _default


def record(event: str, **detail: Any) -> bool:
    return get_log().record(event, **detail)


if __name__ == "__main__":  # python daemon/audit_log.py [path]  -> verify the chain
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path()
    ok, n, message = verify_chain(target)
    print(f"{target}: {'OK' if ok else 'BROKEN'} ({n} records) - {message}")
    raise SystemExit(0 if ok else 1)
