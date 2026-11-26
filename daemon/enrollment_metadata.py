"""Read enrollment metadata without importing the camera or recognition stack."""
import json
from pathlib import Path

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def load_enrollment_metadata(path: Path) -> dict:
    raw = Path(path).read_bytes()
    try:
        import win32crypt

        decoded = win32crypt.CryptUnprotectData(raw, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN)[1]
    except Exception:
        decoded = raw  # Legacy enrollment metadata was plain UTF-8 JSON.
    result = json.loads(decoded.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Enrollment metadata must contain a JSON object")
    return result
