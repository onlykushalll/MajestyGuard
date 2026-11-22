"""Slow, bounded growth of the owner's face templates.

Faces drift (haircut, glasses, tan, lighting in the room you actually sit in), and
a fixed enrollment gets less reliable. This keeps a small, separate pool of extra
templates learned from *confident* unlocks, under rules built so that a mistake or
an attacker cannot walk the identity away from the original enrollment:

* Enrollment ("anchor") templates are never modified, replaced or deleted here.
* A candidate is admitted only when the match is clearly the owner (well above the
  accept threshold), liveness and image quality are high, exactly one face is in
  view, and the face has been held steadily for several frames.
* It must also stay close to the anchors' centroid, so a chain of small steps cannot
  drift away (no "boiling frog").
* It must add something: near-duplicates of existing templates are ignored.
* Slow and capped: a minimum gap between additions, a daily limit, a hard pool
  size with oldest-first eviction.
* At match time an adaptive template can only *help* by a bounded amount over the
  best anchor and always pays a small penalty, so it cannot carry a face the
  anchors reject.
* Stored protected (DPAPI) and tied to the anchors' fingerprint: re-enrolling
  discards the pool. ``clear()`` wipes it.

Pure numpy; no camera, no Qt.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# Admission
MATCH_MIN = 0.80            # vs best anchor (accept threshold is 0.75)
ANCHOR_CENTROID_MIN = 0.70  # vs the anchor centroid: the drift guard
NOVELTY_MAX = 0.93          # vs every existing template: skip near-duplicates
LIVENESS_MIN = 0.85
QUALITY_MIN = 0.60
STEADY_FRAMES = 3
# Pacing
MIN_GAP_S = 20 * 60
MAX_PER_DAY = 3
MAX_POOL = 12
# Matching influence
PENALTY = 0.02
MAX_BOOST = 0.08

Protect = Callable[[bytes], bytes]


def fingerprint(anchors: np.ndarray) -> str:
    a = np.ascontiguousarray(np.asarray(anchors, dtype=np.float32))
    return hashlib.sha256(a.tobytes()).hexdigest()[:32]


def _unit(v) -> Optional[np.ndarray]:
    try:
        arr = np.asarray(v, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    n = float(np.linalg.norm(arr))
    if not np.isfinite(n) or n < 1e-6:
        return None
    return arr / n


def _dpapi_protect(data: bytes) -> bytes:
    import win32crypt  # type: ignore
    return win32crypt.CryptProtectData(data, "MajestyGuard adaptive templates", None, None, None, 0)


def _dpapi_unprotect(data: bytes) -> bytes:
    import win32crypt  # type: ignore
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


class AdaptiveTemplateStore:
    def __init__(
        self,
        path: Optional[Path],
        anchors: np.ndarray,
        *,
        protect: Optional[Protect] = None,
        unprotect: Optional[Protect] = None,
        clock: Callable[[], float] = time.time,
        enabled: bool = True,
        max_pool: int = MAX_POOL,
    ) -> None:
        self.enabled = bool(enabled)
        self._path = Path(path) if path else None
        self._clock = clock
        self._max_pool = max(1, int(max_pool))
        self._protect = protect
        self._unprotect = unprotect
        if self._path is not None and protect is None:
            self._protect, self._unprotect = _dpapi_protect, _dpapi_unprotect
        anchors = np.asarray(anchors, dtype=np.float32)
        rows = [u for u in (_unit(r) for r in anchors) if u is not None]
        self._anchors = np.stack(rows) if rows else np.zeros((0, 0), dtype=np.float32)
        centroid = _unit(self._anchors.mean(axis=0)) if len(rows) else None
        self._centroid = centroid
        self._fp = fingerprint(self._anchors) if len(rows) else ""
        self._pool: list[np.ndarray] = []
        self._added_at: list[float] = []
        self._steady = 0
        if self.enabled:
            self._load()

    # ---------------------------------------------------------------- queries
    @property
    def size(self) -> int:
        return len(self._pool)

    def matrix(self) -> Optional[np.ndarray]:
        return np.stack(self._pool) if (self.enabled and self._pool) else None

    def boosted(self, embedding: np.ndarray, anchor_best: float) -> float:
        """Best-of(anchor, adaptive - penalty), never more than MAX_BOOST above the anchors."""
        if not self.enabled or not self._pool:
            return anchor_best
        e = _unit(embedding)
        if e is None:
            return anchor_best
        adaptive = float(np.max(np.stack(self._pool) @ e)) - PENALTY
        return float(min(max(anchor_best, adaptive), anchor_best + MAX_BOOST))

    # -------------------------------------------------------------- admission
    def consider(
        self,
        embedding: np.ndarray,
        *,
        anchor_score: float,
        liveness: float,
        quality: float,
        face_count: int,
        owner_confirmed: bool,
        full_liveness: bool = True,
    ) -> bool:
        """Offer one confident owner frame. True only when a template was added."""
        if not self.enabled or self._anchors.size == 0:
            return False
        ok = (
            owner_confirmed and full_liveness and face_count == 1
            and liveness >= LIVENESS_MIN and quality >= QUALITY_MIN
            and anchor_score >= MATCH_MIN
        )
        if not ok:
            self._steady = 0
            return False
        self._steady += 1
        if self._steady < STEADY_FRAMES:
            return False
        self._steady = 0                          # every outcome below starts a fresh streak
        e = _unit(embedding)
        if e is None or e.size != self._anchors.shape[1]:
            return False
        now = self._clock()
        if self._added_at and now - self._added_at[-1] < MIN_GAP_S:
            return False
        if sum(1 for t in self._added_at if now - t < 86400.0) >= MAX_PER_DAY:
            return False
        if self._centroid is None or float(self._centroid @ e) < ANCHOR_CENTROID_MIN:
            return False
        existing = self._anchors if not self._pool else np.vstack([self._anchors, np.stack(self._pool)])
        if float(np.max(existing @ e)) >= NOVELTY_MAX:
            return False
        self._pool.append(e)
        self._added_at.append(now)
        while len(self._pool) > self._max_pool:
            self._pool.pop(0)
            self._added_at.pop(0)
        self._save()
        return True

    def clear(self) -> None:
        self._pool.clear()
        self._added_at.clear()
        self._steady = 0
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        if self._path is None or self._protect is None:
            return
        try:
            buf = io.BytesIO()
            np.savez(buf, pool=np.stack(self._pool), added=np.asarray(self._added_at, dtype=np.float64),
                     fp=np.frombuffer(self._fp.encode(), dtype=np.uint8))
            blob = self._protect(buf.getvalue())
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, self._path)
        except Exception:
            pass          # losing the pool is harmless; it simply regrows

    def _load(self) -> None:
        if self._path is None or self._unprotect is None or not self._path.is_file():
            return
        try:
            raw = self._unprotect(self._path.read_bytes())
            z = np.load(io.BytesIO(raw), allow_pickle=False)
            if bytes(z["fp"].tobytes()).decode() != self._fp:
                self.clear()                      # anchors changed (re-enrolled): start over
                return
            pool = np.asarray(z["pool"], dtype=np.float32)
            added = [float(t) for t in z["added"]]
            if pool.ndim != 2 or pool.shape[1] != self._anchors.shape[1] or len(added) != len(pool):
                self.clear()
                return
            now = self._clock()
            for row, t in zip(pool, added):
                u = _unit(row)
                # Re-apply the drift guard on load: a tampered file cannot smuggle a stranger in.
                if u is None or t > now + 3600.0 or float(self._centroid @ u) < ANCHOR_CENTROID_MIN:
                    continue
                self._pool.append(u)
                self._added_at.append(t)
            self._pool = self._pool[-self._max_pool:]
            self._added_at = self._added_at[-self._max_pool:]
        except Exception:
            self._pool.clear()
            self._added_at.clear()
