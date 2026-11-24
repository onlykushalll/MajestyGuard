"""Resolve built-in diagnostic tools without trusting PATH or SystemRoot."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=3)
def system_tool(name: str) -> str:
    import win32api

    parts = {
        "wmic": ("wbem", "WMIC.exe"),
        "powershell": ("WindowsPowerShell", "v1.0", "powershell.exe"),
        "pnputil": ("pnputil.exe",),
    }[name]
    directory = Path(win32api.GetSystemDirectory())
    if not directory.is_absolute():
        raise OSError("Windows did not return an absolute system directory")
    return str(directory.joinpath(*parts))
