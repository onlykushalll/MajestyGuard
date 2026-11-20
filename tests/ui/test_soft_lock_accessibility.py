"""Sticky/Toggle/Filter Keys hotkeys must be restored to the user's ORIGINAL
state even when the lock is re-engaged without an unlock in between."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui"))

import soft_lock  # noqa: E402

_ORIGINAL = 0x00000004 | 0x00000001  # hotkey active + feature flag


class _FakeUser32:
    def __init__(self):
        self.state = {
            soft_lock.SPI_GETSTICKYKEYS: _ORIGINAL,
            soft_lock.SPI_GETTOGGLEKEYS: _ORIGINAL,
            soft_lock.SPI_GETFILTERKEYS: _ORIGINAL,
        }

    def SystemParametersInfoW(self, action, _size, ref, _flags):
        get = {
            soft_lock.SPI_SETSTICKYKEYS: soft_lock.SPI_GETSTICKYKEYS,
            soft_lock.SPI_SETTOGGLEKEYS: soft_lock.SPI_GETTOGGLEKEYS,
            soft_lock.SPI_SETFILTERKEYS: soft_lock.SPI_GETFILTERKEYS,
        }
        obj = ref._obj
        if action in self.state:
            obj.dwFlags = self.state[action]
        elif action in get:
            self.state[get[action]] = obj.dwFlags
        return 1


class _FakeWindll:
    def __init__(self):
        self.user32 = _FakeUser32()


def _install_fake(monkeypatch):
    fake = _FakeWindll()
    monkeypatch.setattr(soft_lock.ctypes, "windll", fake, raising=False)
    monkeypatch.setattr(soft_lock, "_saved_stickykeys", None)
    monkeypatch.setattr(soft_lock, "_saved_togglekeys", None)
    monkeypatch.setattr(soft_lock, "_saved_filterkeys", None)
    return fake.user32


def _hotkey_flags(user32):
    return list(user32.state.values())


def test_disable_then_restore_returns_original(monkeypatch):
    user32 = _install_fake(monkeypatch)
    soft_lock._disable_accessibility_shortcuts()
    assert all(not (v & soft_lock._HOTKEYACTIVE_BIT) for v in _hotkey_flags(user32))
    soft_lock._restore_accessibility_shortcuts()
    assert _hotkey_flags(user32) == [_ORIGINAL] * 3


def test_repeated_disable_still_restores_original(monkeypatch):
    user32 = _install_fake(monkeypatch)
    soft_lock._disable_accessibility_shortcuts()
    soft_lock._disable_accessibility_shortcuts()  # lock re-engaged, no unlock between
    soft_lock._restore_accessibility_shortcuts()
    assert _hotkey_flags(user32) == [_ORIGINAL] * 3
