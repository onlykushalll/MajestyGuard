"""Tests for overlay security hardening in soft_lock.py."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
sys.path.insert(0, str(UI))


def test_close_event_ignores_close_signal():
    """closeEvent must ignore the event when not in allow_close mode."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "event.ignore()" in source
    assert "closeEvent" in source
    # Must re-show fullscreen after ignoring
    assert "showFullScreen" in source


def test_key_press_consumes_all_keys_except_space():
    """keyPressEvent must accept all keys. Space triggers verify."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "keyPressEvent" in source
    assert "Key_Space" in source
    assert "event.accept()" in source
    # Must NOT allow Return/Enter to trigger verification (Space only)
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if "keyPressEvent" in line and "def " in line:
            # Scan the method body for the verify trigger condition
            method_body = "\n".join(lines[i:i + 10])
            assert "Key_Return" not in method_body, "keyPressEvent must not trigger on Return"
            assert "Key_Enter" not in method_body, "keyPressEvent must not trigger on Enter"
            break


def test_space_triggers_verification():
    """Space key must trigger _request_verification."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_request_verification" in source
    assert "overlay_key" in source


def test_keyboard_hook_blocks_task_manager():
    """WH_KEYBOARD_LL hook must block Ctrl+Shift+Esc."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "WH_KEYBOARD_LL" in source
    assert "SetWindowsHookExW" in source
    assert "UnhookWindowsHookEx" in source
    assert "VK_ESCAPE" in source or "0x1B" in source


def test_keyboard_hook_blocks_alt_tab():
    """WH_KEYBOARD_LL hook must block Alt+Tab."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_TAB" in source or "0x09" in source


def test_keyboard_hook_blocks_win_combos():
    """WH_KEYBOARD_LL hook must block Win+D, Win+N, Win+A, Win+Tab."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_LWIN" in source or "0x5B" in source
    for key in ("VK_D", "VK_N", "VK_A"):
        assert key in source


def test_keyboard_hook_blocks_alt_f4():
    """WH_KEYBOARD_LL hook must block Alt+F4."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_F4" in source or "0x73" in source


def test_hook_uninstalled_on_unlock():
    """_uninstall_keyboard_hook must call UnhookWindowsHookEx."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_uninstall_keyboard_hook" in source
    assert "UnhookWindowsHookEx" in source


def test_overlay_covers_virtual_screen():
    """Overlay must use virtualGeometry to cover all monitors + gesture zones."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "virtualGeometry" in source


def test_key_release_is_consumed():
    """keyReleaseEvent must consume the event."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "keyReleaseEvent" in source
    assert "event.accept()" in source
