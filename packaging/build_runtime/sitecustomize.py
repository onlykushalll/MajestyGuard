"""Process-local CRT pinning for Windows PyInstaller import probes only."""

import os

if os.name == "nt" and (directory := os.environ.get("MG_BUILD_CRT_DIR")):
    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    root = Path(directory)
    try:
        if not root.is_absolute():
            raise ValueError("Build CRT path must be absolute")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetErrorMode.argtypes = [wintypes.UINT]
        kernel32.SetErrorMode.restype = wintypes.UINT
        # Return a failing exit code instead of stranding a Python probe in WER.
        kernel32.SetErrorMode(0x0001 | 0x0002)
        _crt_search = os.add_dll_directory(str(root))
        _crt_handles = [
            ctypes.WinDLL(str(root / name))
            for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
        ]
    except (OSError, ValueError) as exc:
        import sys

        sys.stderr.write(f"Could not load verified build CRT: {exc}\n")
        sys.stderr.flush()
        os._exit(1)
