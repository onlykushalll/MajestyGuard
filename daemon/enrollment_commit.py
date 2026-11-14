"""Crash-safe commit of a coverage-enrollment gallery.

``commit_gallery`` is the only place the continuous enrollment writes biometric
data. It never runs unless the caller hands it an ``ok`` ``CoverageResult``
(``commit_result``), it keeps a DPAPI-protected backup of whatever gallery and
metadata exist, writes each file via ``tmp -> os.replace``, and restores both
files if any step fails. Protect/unprotect are injectable so tests need neither
Windows nor win32crypt.
"""
from __future__ import annotations

import io
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    from .enrollment_coverage import CoverageConfig, CoverageResult
except ImportError:  # run as a script from daemon/
    from enrollment_coverage import CoverageConfig, CoverageResult

SELECTION_POLICY = "continuous_coverage_then_quality_diversity"
METADATA_VERSION = 6
GALLERY_DESCRIPTION = "MajestyGuard biometric embeddings"
META_DESCRIPTION = "MajestyGuard enrollment metadata"


def _win_protect(data: bytes, description: str) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, description, None, None, None, 0)


def _win_is_protected(data: bytes) -> bool:
    import win32crypt

    try:
        win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return True
    except Exception:
        return False


@dataclass
class CommitOutcome:
    ok: bool
    error: str = ""
    backups: dict = field(default_factory=dict)
    rollback_errors: list = field(default_factory=list)
    rolled_back: bool = False


def build_metadata(result: CoverageResult, config: Optional[CoverageConfig] = None, *,
                   profile: str = "continuous-coverage") -> dict:
    cfg = config or CoverageConfig()
    required = [b for b in sorted(result.bucket_counts)]
    selected = []
    for i, s in enumerate(result.selected):
        row = s.metadata()
        row["angle"] = s.bucket          # keeps older diagnostics able to group by view
        row["sample_index"] = i + 1
        selected.append(row)
    return {
        "version": METADATA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "selection_policy": SELECTION_POLICY,
        "angles": required,
        "min_angle_count": len(required),
        "captured_count": result.captured_count,
        "pruned_count": result.pruned_count,
        "selected_count": len(result.selected),
        "max_templates": cfg.max_templates,
        "min_total": cfg.min_total,
        "pitch_reliable": bool(result.pitch_reliable),
        "bucket_counts": dict(result.bucket_counts),
        "thresholds": {
            "min_det": cfg.min_det,
            "min_quality": cfg.min_quality,
            "liveness": cfg.liveness_threshold,
            "min_identity_cos": cfg.min_identity_cos,
            "dup_similarity": cfg.dup_similarity,
            "min_cohesion": cfg.min_cohesion,
        },
        "selected_samples": selected,
    }


def _validate(arr: np.ndarray) -> str:
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 2:
        return "bad_shape"
    if not np.all(np.isfinite(arr)):
        return "non_finite"
    norms = np.linalg.norm(arr, axis=1)
    if float(np.abs(norms - 1.0).max()) > 1e-3:
        return "not_normalized"
    return ""


def backup_existing(paths: list[Path], *, protect: Callable[[bytes, str], bytes],
                    is_protected: Callable[[bytes], bool]) -> dict[Path, Path]:
    backups: dict[Path, Path] = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for path in paths:
        if not path.exists():
            continue
        data = path.read_bytes()
        if not is_protected(data):
            data = protect(data, "MajestyGuard backup")
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        backup.write_bytes(data)
        shutil.copystat(path, backup)
        backups[path] = backup
    return backups


def rollback(targets: list[Path], backups: dict[Path, Path]) -> list[str]:
    """Restore each target from its backup, or remove it if none existed."""
    errors: list[str] = []
    for target in targets:
        tmp = target.with_name(f"{target.name}.rollback-{os.getpid()}.tmp")
        try:
            backup = backups.get(target)
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, tmp)
                os.replace(tmp, target)
        except OSError as exc:
            errors.append(f"{target}: {exc}")
        finally:
            tmp.unlink(missing_ok=True)
    return errors


def commit_gallery(
    embeddings: np.ndarray,
    metadata: dict,
    *,
    gallery_path: Path,
    meta_path: Path,
    protect: Optional[Callable[[bytes, str], bytes]] = None,
    is_protected: Optional[Callable[[bytes], bool]] = None,
    allow_plaintext: bool = False,
) -> CommitOutcome:
    protect = protect or _win_protect
    is_protected = is_protected or _win_is_protected
    gallery_path, meta_path = Path(gallery_path), Path(meta_path)

    arr = np.array(embeddings, dtype=np.float32, copy=True)
    try:
        problem = _validate(arr)
        if problem:
            return CommitOutcome(False, f"invalid_embeddings:{problem}")

        gallery_path.parent.mkdir(parents=True, exist_ok=True)
        backups = backup_existing([gallery_path, meta_path], protect=protect, is_protected=is_protected)
        targets = [gallery_path, meta_path]
        tmp_gallery = gallery_path.with_suffix(gallery_path.suffix + ".tmp")
        tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")

        try:
            buf = io.BytesIO()
            np.save(buf, arr, allow_pickle=False)
            raw = buf.getvalue()
            try:
                payload = protect(raw, GALLERY_DESCRIPTION)
            except Exception as exc:
                if not allow_plaintext:
                    return CommitOutcome(False, f"encryption_failed:{exc}", backups=backups)
                payload = raw
            tmp_gallery.write_bytes(payload)
            os.replace(tmp_gallery, gallery_path)

            meta_bytes = json.dumps(metadata, indent=2).encode("utf-8")
            tmp_meta.write_bytes(protect(meta_bytes, META_DESCRIPTION))
            os.replace(tmp_meta, meta_path)
        except Exception as exc:
            errors = rollback(targets, backups)
            return CommitOutcome(False, f"write_failed:{exc}", backups=backups,
                                 rollback_errors=errors, rolled_back=not errors)
        finally:
            tmp_gallery.unlink(missing_ok=True)
            tmp_meta.unlink(missing_ok=True)
        return CommitOutcome(True, backups=backups)
    finally:
        arr.fill(0)


def commit_result(
    result: CoverageResult,
    config: Optional[CoverageConfig] = None,
    *,
    gallery_path: Path,
    meta_path: Path,
    **kwargs,
) -> CommitOutcome:
    """Commit only a fully validated result; anything else touches nothing."""
    if not result.ok or not result.selected:
        return CommitOutcome(False, f"not_committed:{result.reason or 'empty'}")
    emb = result.embeddings()
    try:
        return commit_gallery(
            emb,
            build_metadata(result, config),
            gallery_path=gallery_path,
            meta_path=meta_path,
            **kwargs,
        )
    finally:
        emb.fill(0)
