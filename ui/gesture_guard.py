"""Turn off touchpad multi-finger gestures while the desktop is locked.

Windows Precision Touchpad three/four-finger swipes (Task View, switch
desktops, show desktop, Search/Action Center taps) are handled by the shell, not
as key presses, so the keyboard/mouse hooks never see them. A four-finger swipe
exposed Task View over the overlay. The gestures are user settings stored under
HKCU, so the lock saves them, sets them to "Nothing", and puts the user's
original values back on unlock.

Crash safety: the originals are written to a restore file *before* anything is
changed, and ``recover_after_crash`` (called at UI start) restores from it, so a
crashed lock cannot leave the user's gestures permanently disabled.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\PrecisionTouchPad"
NAMES = (
    "ThreeFingerSlideEnabled", "FourFingerSlideEnabled",
    "ThreeFingerTapEnabled", "FourFingerTapEnabled",
    "ThreeFingerUp", "ThreeFingerDown", "ThreeFingerLeft", "ThreeFingerRight",
    "FourFingerUp", "FourFingerDown", "FourFingerLeft", "FourFingerRight",
)
_NOTHING = 0


class WinRegistry:
    """HKCU\\...\\PrecisionTouchPad DWORD access. Missing values read as None."""

    def get(self, name: str) -> Optional[int]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUBKEY, 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, name)
                return int(value) if kind == winreg.REG_DWORD else None
        except FileNotFoundError:
            return None

    def set(self, name: str, value: int) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value) & 0xFFFFFFFF)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def default_restore_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData"))
    return Path(base) / "MajestyGuard" / "gesture_restore.json"


class GestureGuard:
    def __init__(self, registry=None, restore_path: Optional[Path] = None) -> None:
        self._reg = registry if registry is not None else WinRegistry()
        self._path = Path(restore_path) if restore_path is not None else default_restore_path()
        self._saved: Optional[Dict[str, Optional[int]]] = None

    @property
    def engaged(self) -> bool:
        return self._saved is not None

    # ------------------------------------------------------------------ engage
    def engage(self) -> bool:
        """Disable gestures. Idempotent: a repeat call never overwrites the saved originals."""
        if self._saved is not None:
            return True
        try:
            leftover = self._read_restore_file()
            saved = {}
            for name in NAMES:
                # If a previous lock crashed, the live value is already "Nothing";
                # the file still holds what the user really had.
                saved[name] = leftover[name] if name in leftover else self._reg.get(name)
            self._write_restore_file(saved)           # persist BEFORE changing anything
            self._saved = saved
            for name in NAMES:
                self._reg.set(name, _NOTHING)
            return True
        except Exception:
            self._restore_quietly()
            return False

    # ----------------------------------------------------------------- restore
    def restore(self) -> bool:
        if self._saved is None:
            return False
        ok = self._apply(self._saved)
        if ok:
            self._saved = None
            self._remove_restore_file()
        return ok

    def recover_after_crash(self) -> bool:
        """Call at UI start: if a lock died while gestures were off, put them back."""
        if self._saved is not None:
            return False
        leftover = self._read_restore_file()
        if not leftover:
            return False
        ok = self._apply(leftover)
        if ok:
            self._remove_restore_file()
        return ok

    # ----------------------------------------------------------------- helpers
    def _apply(self, saved: Dict[str, Optional[int]]) -> bool:
        ok = True
        for name, value in saved.items():
            try:
                if value is None:
                    self._reg.delete(name)
                else:
                    self._reg.set(name, value)
            except Exception:
                ok = False
        return ok

    def _restore_quietly(self) -> None:
        if self._saved is not None:
            self._apply(self._saved)
            self._saved = None
            self._remove_restore_file()

    def _read_restore_file(self) -> Dict[str, Optional[int]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {k: (None if v is None else int(v)) for k, v in raw.items() if k in NAMES}
        except (OSError, ValueError, AttributeError):
            return {}

    def _write_restore_file(self, saved: Dict[str, Optional[int]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        os.replace(tmp, self._path)

    def _remove_restore_file(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass
