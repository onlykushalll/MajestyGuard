"""Safety rails around replacing the enrolled face gallery.

Pure logic (numpy + stdlib; DPAPI is injected) so it is testable without Windows.

* Owner continuity: a new enrollment may only replace an existing gallery when the new
  face is recognisably the enrolled one. Without this, anyone at an unlocked desktop could
  press "Re-enroll" and make their own face the owner. Someone whose look changed a lot
  starts over through an explicit reset instead (``MG_ENROLL_CONTINUITY=0``, set only by the
  Control Center after Windows re-authentication).
* Backups: every enrollment keeps a DPAPI-protected copy of what it replaced. They are
  pruned (old faces must not pile up on disk) and the newest *valid* one can bring back a
  gallery that has become unreadable.
* Result file: a tiny non-biometric ``enroll_result.json`` so the Control Center can say
  what happened without ever seeing biometric data.
"""
from __future__ import annotations

import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

import numpy as np

CONTINUITY_ENV = "MG_ENROLL_CONTINUITY"
RESULT_FILE = "enroll_result.json"

# Cosine similarity thresholds for InsightFace buffalo_l / w600k_r50 embeddings: impostors sit
# well below ~0.30, the same person across lighting/pose/glasses changes stays above ~0.45.
MEDIAN_BEST_MIN = 0.45
PER_TEMPLATE_MIN = 0.35
FRACTION_MIN = 0.70
KEEP_BACKUPS = 2

Unprotect = Callable[[bytes], bytes]


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str            # "first_enrollment" | "existing_unreadable" | "continuity_ok" |
    #                        "continuity_waived" | "different_person" | "invalid_new_gallery"
    median_best: float = 0.0
    fraction: float = 0.0


def continuity_required(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(CONTINUITY_ENV, "1")).strip() != "0"


def _default_unprotect(data: bytes) -> bytes:
    import win32crypt  # type: ignore

    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


def _normalised(arr: np.ndarray) -> Optional[np.ndarray]:
    try:
        out = np.asarray(arr, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if out.ndim != 2 or out.shape[0] < 1 or out.shape[1] < 2 or not np.all(np.isfinite(out)):
        return None
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    if float(norms.min()) < 1e-6:
        return None
    return out / norms


def read_gallery(path: Path, unprotect: Optional[Unprotect] = None) -> tuple[str, Optional[np.ndarray]]:
    """('none', None) when absent, ('ok', array) when it decrypts and validates, else ('unreadable', None).

    Never falls back to treating the bytes as plaintext.
    """
    path = Path(path)
    if not path.is_file():
        return "none", None
    unprotect = unprotect or _default_unprotect
    try:
        raw = unprotect(path.read_bytes())
        arr = _normalised(np.load(io.BytesIO(raw), allow_pickle=False))
    except Exception:
        return "unreadable", None
    return ("ok", arr) if arr is not None else ("unreadable", None)


def check_continuity(
    new_gallery: np.ndarray,
    existing_path: Path,
    *,
    required: Optional[bool] = None,
    unprotect: Optional[Unprotect] = None,
) -> Verdict:
    """May ``new_gallery`` replace whatever is stored at ``existing_path``?"""
    new = _normalised(new_gallery)
    if new is None:
        return Verdict(False, "invalid_new_gallery")
    status, old = read_gallery(existing_path, unprotect)
    if status == "none":
        return Verdict(True, "first_enrollment")
    if status == "unreadable":
        # Nothing usable is protecting the PC today (the daemon already treats this as "not
        # enrolled"), so there is no owner to be continuous with.
        return Verdict(True, "existing_unreadable")
    best = (new @ old.T).max(axis=1)                    # each new template vs its closest old one
    median = float(np.median(best))
    fraction = float(np.mean(best >= PER_TEMPLATE_MIN))
    if median >= MEDIAN_BEST_MIN and fraction >= FRACTION_MIN:
        return Verdict(True, "continuity_ok", median, fraction)
    if required is None:
        required = continuity_required()
    if not required:
        return Verdict(True, "continuity_waived", median, fraction)
    return Verdict(False, "different_person", median, fraction)


# ----------------------------------------------------------------------------- backups
def _backup_sort_key(path: Path) -> str:
    return path.name                                    # names embed %Y%m%d-%H%M%S-%f: lexicographic = chronological


def list_backups(gallery_path: Path) -> list[Path]:
    gallery_path = Path(gallery_path)
    pattern = f"{gallery_path.stem}.backup-*{gallery_path.suffix}"
    return sorted(gallery_path.parent.glob(pattern), key=_backup_sort_key)


def prune_backups(paths: list[Path], keep: int = KEEP_BACKUPS) -> list[Path]:
    """Delete all but the ``keep`` newest backups of each file. Returns what was removed."""
    removed: list[Path] = []
    for target in paths:
        backups = list_backups(target)
        for old in backups[: max(0, len(backups) - max(0, int(keep)))]:
            try:
                old.unlink()
                removed.append(old)
            except OSError:
                pass
    return removed


def restore_latest_valid_backup(
    gallery_path: Path,
    meta_path: Optional[Path] = None,
    *,
    unprotect: Optional[Unprotect] = None,
) -> Optional[Path]:
    """Bring back the newest backup that decrypts and validates, but only if the live
    gallery is unusable. Returns the backup used, or None (live gallery fine / no good backup)."""
    gallery_path = Path(gallery_path)
    status, _ = read_gallery(gallery_path, unprotect)
    if status == "ok":
        return None
    for candidate in reversed(list_backups(gallery_path)):
        if read_gallery(candidate, unprotect)[0] != "ok":
            continue
        tmp = gallery_path.with_name(gallery_path.name + f".restore-{os.getpid()}.tmp")
        try:
            tmp.write_bytes(candidate.read_bytes())
            os.replace(tmp, gallery_path)
        except OSError:
            tmp.unlink(missing_ok=True)
            continue
        if meta_path is not None:
            _restore_meta(Path(meta_path), candidate)
        return candidate
    return None


def _restore_meta(meta_path: Path, gallery_backup: Path) -> None:
    """Metadata backups share the gallery backup's timestamp; restore the matching one if present."""
    stamp = gallery_backup.name.split(".backup-", 1)[-1].rsplit(".", 1)[0]
    mate = meta_path.with_name(f"{meta_path.stem}.backup-{stamp}{meta_path.suffix}")
    if not mate.is_file():
        return
    tmp = meta_path.with_name(meta_path.name + f".restore-{os.getpid()}.tmp")
    try:
        tmp.write_bytes(mate.read_bytes())
        os.replace(tmp, meta_path)
    except OSError:
        tmp.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- result file
def write_result(state_dir: Path, outcome: str, detail: str = "") -> bool:
    """outcome: 'ok' | 'rejected_different_person' | 'failed'. Never contains biometric data."""
    path = Path(state_dir) / RESULT_FILE
    payload = {"outcome": str(outcome)[:40], "detail": str(detail)[:160], "ts": round(time.time(), 3)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def read_result(state_dir: Path, max_age_s: float = 600.0) -> Optional[dict]:
    try:
        data = json.loads((Path(state_dir) / RESULT_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or time.time() - float(data.get("ts", 0)) > max_age_s:
            return None
        return {"outcome": str(data.get("outcome", "")), "detail": str(data.get("detail", "")), "ts": float(data["ts"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None
