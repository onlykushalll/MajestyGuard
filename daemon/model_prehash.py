"""Background SHA-256 of the ONNX model packs, overlapped with start-up imports.

Model integrity (TOFU/reference pinning) needs the SHA-256 of ~250 MB of ONNX
files before they may be loaded. Hashing them on the critical path cost 1-3 s;
the daemon now starts hashing on worker threads as the very first thing it does
and the integrity check simply collects the finished digests. ``hashlib``
releases the GIL while hashing, so this genuinely overlaps with importing
OpenCV / ONNX Runtime / InsightFace. The check itself is unchanged: the same
digest is compared against the same pins, the work just happens earlier.
"""
from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Optional

_CHUNK = 1 << 20
_LOCK = threading.Lock()
_FUTURES: dict[str, Future] = {}
_EXECUTOR: Optional[ThreadPoolExecutor] = None


def cache_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="mg-model-hash")
    return _EXECUTOR


def _onnx_files(directory: str) -> list[str]:
    found: list[str] = []
    if not directory or not os.path.isdir(directory):
        return found
    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in sorted(filenames):
            if filename.lower().endswith(".onnx"):
                found.append(os.path.join(dirpath, filename))
    return found


def start(*directories: "str | Path") -> int:
    """Begin hashing every ``*.onnx`` under the directories. Returns files queued."""
    queued = 0
    with _LOCK:
        for directory in directories:
            for path in _onnx_files(str(directory)):
                key = cache_key(path)
                if key in _FUTURES:
                    continue
                _FUTURES[key] = _executor().submit(sha256_file, path)
                queued += 1
    return queued


def digest_for(path: str) -> str:
    """SHA-256 of ``path``, using (and waiting for) a background hash if queued.

    Raises ``OSError`` exactly like reading the file directly would, so the
    caller's fail-closed handling is unchanged. A finished digest is consumed:
    a later call re-hashes the file, so a swapped model is never excused by a
    stale cached value.
    """
    key = cache_key(path)
    with _LOCK:
        future = _FUTURES.pop(key, None)
    if future is None:
        return sha256_file(path)
    return future.result()
