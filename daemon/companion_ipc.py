r"""
companion_ipc.py — Python WHCDF bridge server for MajestyGuard.

Listens on \\.\pipe\MajestyGuard_WHCDF.
C# UnlockTask connects when the lock screen activates, sends three nonces,
and expects an HMAC-SHA256 back if the face is currently authorized.

Protocol (line-delimited, UTF-8):
  C# → Python:
    WHCDF_HMAC_REQUEST
    <serviceNonce_hex>
    <deviceNonce_hex>
    <sessionNonce_hex>

  Python → C# (success):
    HMAC_OK
    <hmac_hex>

  Python → C# (denied):
    HMAC_DENIED
    <reason>

IMPORTANT: asyncio.create_task() is used (not deprecated ensure_future).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Optional

import win32file   # type: ignore
import win32pipe   # type: ignore
import win32security  # type: ignore
import pywintypes  # type: ignore

log = logging.getLogger(__name__)

PIPE_NAME = r"\\.\pipe\MajestyGuard_WHCDF"

# ---------------------------------------------------------------------------
# Shared face-state — updated by the daemon's recognition loop
# ---------------------------------------------------------------------------

class FaceState:
    """Thread-safe singleton holding the daemon's current recognition result."""

    _lock = threading.Lock()
    _user_recognized: bool = False
    _recognized_at: float = 0.0
    _liveness_score: float = 0.0

    # How long (seconds) a recognition result stays valid for unlock purposes.
    # After this window the user must re-verify.
    VALIDITY_WINDOW: float = 30.0

    @classmethod
    def set_recognized(cls, liveness_score: float = 1.0) -> None:
        with cls._lock:
            cls._user_recognized = True
            cls._recognized_at = time.monotonic()
            cls._liveness_score = liveness_score
        log.debug("FaceState: recognized (liveness=%.3f)", liveness_score)

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._user_recognized = False
            cls._recognized_at = 0.0
            cls._liveness_score = 0.0
        log.debug("FaceState: cleared")

    @classmethod
    def is_authorized(cls) -> tuple[bool, str]:
        """Return (authorized, reason_string)."""
        with cls._lock:
            if not cls._user_recognized:
                return False, "face-not-recognized"
            age = time.monotonic() - cls._recognized_at
            if age > cls.VALIDITY_WINDOW:
                cls._user_recognized = False  # expire
                return False, f"recognition-expired ({age:.1f}s > {cls.VALIDITY_WINDOW}s)"
            if cls._liveness_score < 0.82:
                return False, f"liveness-below-threshold ({cls._liveness_score:.3f})"
            return True, "ok"


# ---------------------------------------------------------------------------
# HMAC key — loaded once from env or generated per-session
# ---------------------------------------------------------------------------

_MUTUAL_AUTH_KEY: Optional[bytes] = None
_MUTUAL_AUTH_KEY_LOCK = threading.Lock()


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_mutual_auth_key() -> Optional[bytes]:
    """
    Fail closed until WHCDF has a secure DPAPI/Credential Manager handoff.

    A machine/user environment variable is intentionally ignored by default
    because any local process in the same account boundary can discover it.
    It is accepted only for isolated development tests with an explicit opt-in.
    """
    raw = os.environ.get("MAJESTYGUARD_MUTUAL_AUTH_KEY", "")
    if raw:
        if not _env_enabled("MG_ALLOW_INSECURE_WHCDF_ENV_KEY"):
            log.error(
                "Ignoring MAJESTYGUARD_MUTUAL_AUTH_KEY because environment "
                "storage is not a secure WHCDF key source. Set "
                "MG_ALLOW_INSECURE_WHCDF_ENV_KEY=1 only for isolated dev tests."
            )
            return None
        try:
            key = bytes.fromhex(raw)
            if len(key) == 32:
                log.warning("MutualAuthKey loaded from insecure environment dev override")
                return key
            log.warning("MutualAuthKey env-var wrong length (%d bytes)", len(key))
        except ValueError:
            log.warning("MutualAuthKey env-var is not valid hex")

    log.error("WHCDF MutualAuthKey not securely configured; denying HMAC requests")
    return None


def _get_mutual_auth_key() -> Optional[bytes]:
    global _MUTUAL_AUTH_KEY
    with _MUTUAL_AUTH_KEY_LOCK:
        if _MUTUAL_AUTH_KEY is None:
            _MUTUAL_AUTH_KEY = _load_mutual_auth_key()
        return _MUTUAL_AUTH_KEY


# ---------------------------------------------------------------------------
# Pipe helpers (synchronous, runs in executor thread)
# ---------------------------------------------------------------------------

def _local_pipe_clients_allowed() -> bool:
    """
    WHCDF IPC is login-adjacent. Until caller identity is verified with a real
    companion SID/app identity check, local clients must be explicitly enabled.
    """
    return _env_enabled("MG_WHCDF_ALLOW_LOCAL_PIPE_CLIENTS")


def _build_security_attributes() -> Optional[win32security.SECURITY_ATTRIBUTES]:
    """Use the process default DACL; never install a NULL DACL."""
    return None


def _authorize_hmac_request() -> tuple[Optional[bytes], Optional[str]]:
    """
    Return (key, denial_reason). Check caller/key configuration before reading
    FaceState so unauthenticated local clients cannot probe recognition state.
    """
    if not _local_pipe_clients_allowed():
        log.error("WHCDF: denied - local pipe client authentication is not configured")
        return None, "client-auth-not-configured"

    key = _get_mutual_auth_key()
    if key is None:
        return None, "mutual-auth-key-not-configured"

    authorized, reason = FaceState.is_authorized()
    if not authorized:
        log.warning("WHCDF: denied - %s", reason)
        return None, reason

    return key, None


def _serve_one_connection_sync() -> None:
    """Block until one client connects, handle the HMAC exchange, disconnect."""
    sa = _build_security_attributes()
    handle = win32pipe.CreateNamedPipe(
        PIPE_NAME,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
        win32pipe.PIPE_UNLIMITED_INSTANCES,
        4096, 4096, 0, sa,
    )
    try:
        win32pipe.ConnectNamedPipe(handle, None)

        # Read all lines (up to 4096 bytes covers 4 lines of hex easily)
        _hr, raw = win32file.ReadFile(handle, 4096)
        lines = raw.decode("utf-8").strip().splitlines()

        if len(lines) < 4 or lines[0] != "WHCDF_HMAC_REQUEST":
            _reply(handle, f"HMAC_DENIED\nbad-protocol (got {len(lines)} lines, header={lines[0] if lines else 'empty'})")
            return

        service_nonce_hex = lines[1].strip()
        device_nonce_hex  = lines[2].strip()
        session_nonce_hex = lines[3].strip()

        key, denial_reason = _authorize_hmac_request()
        if denial_reason:
            _reply(handle, f"HMAC_DENIED\n{denial_reason}")
            return
        assert key is not None

        # Compute HMAC-SHA256(MutualAuthKey, serviceNonce || deviceNonce || sessionNonce)
        try:
            msg = bytes.fromhex(service_nonce_hex) + bytes.fromhex(device_nonce_hex) + bytes.fromhex(session_nonce_hex)
        except ValueError as e:
            log.error("WHCDF: nonce parse error — %s", e)
            _reply(handle, f"HMAC_DENIED\nnonce-parse-error: {e}")
            return

        mac = hmac.new(key, msg, hashlib.sha256).hexdigest()
        log.info("WHCDF: authorized — sending HMAC")
        _reply(handle, f"HMAC_OK\n{mac}")

    except pywintypes.error as e:
        log.error("WHCDF pipe error: %s", e)
    finally:
        try:
            win32file.FlushFileBuffers(handle)
        except pywintypes.error:
            pass
        try:
            win32pipe.DisconnectNamedPipe(handle)
        except pywintypes.error:
            pass
        win32file.CloseHandle(handle)


def _reply(handle, text: str) -> None:
    win32file.WriteFile(handle, (text + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# Async server — runs pipe loop in a thread pool so it doesn't block the loop
# ---------------------------------------------------------------------------

async def run_companion_ipc_server(stop_event: Optional[asyncio.Event] = None) -> None:
    """
    Run the WHCDF pipe server until stop_event is set (or forever).
    Call this as an asyncio task from the daemon's main loop.
    """
    log.info("WHCDF companion IPC server starting on %s", PIPE_NAME)
    loop = asyncio.get_running_loop()

    while stop_event is None or not stop_event.is_set():
        try:
            # Run one blocking pipe accept+exchange in thread pool
            await loop.run_in_executor(None, _serve_one_connection_sync)
        except Exception:
            log.exception("WHCDF: unexpected error in connection handler")
            await asyncio.sleep(0.5)

    log.info("WHCDF companion IPC server stopped")


def start_companion_ipc_thread(stop_event: threading.Event) -> threading.Thread:
    """
    Convenience: start the IPC server in a background thread (non-async contexts).
    The daemon's main.py can use this if it's not async-first.
    """
    def _loop():
        while not stop_event.is_set():
            try:
                _serve_one_connection_sync()
            except Exception:
                log.exception("WHCDF thread: connection handler error")
                time.sleep(0.5)
        log.info("WHCDF companion IPC thread exited")

    t = threading.Thread(target=_loop, name="whcdf-ipc", daemon=True)
    t.start()
    return t


def stop_companion_ipc(pipe_name: str = PIPE_NAME) -> None:
    """Wake up ConnectNamedPipe on the companion thread by opening a connection."""
    try:
        handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        win32file.CloseHandle(handle)
    except pywintypes.error:
        pass
