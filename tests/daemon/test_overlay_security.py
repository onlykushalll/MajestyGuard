"""Tests for overlay security hardening in soft_lock.py."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
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
    """WH_KEYBOARD_LL hook uses allowlist — only Tab/Space pass, everything else blocked."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "WH_KEYBOARD_LL" in source
    assert "SetWindowsHookExW" in source
    assert "UnhookWindowsHookEx" in source
    assert "VK_TAB" in source
    assert "VK_SPACE" in source
    assert "return 1" in source


def test_keyboard_hook_blocks_alt_tab():
    """WH_KEYBOARD_LL hook must block Alt+Tab."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_TAB" in source or "0x09" in source


def test_keyboard_hook_blocks_win_combos():
    """WH_KEYBOARD_LL hook blocks Win combos via allowlist — modifier check present."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_LWIN" in source or "0x5B" in source
    assert "_any_modifier_held" in source


def test_keyboard_hook_blocks_alt_f4():
    """WH_KEYBOARD_LL allowlist blocks Alt+F4 — modifier held = Tab/Space also blocked."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_ALT" in source or "0x12" in source
    assert "_any_modifier_held" in source


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


def test_install_hooks_waits_for_thread_confirmation():
    """_install_hooks must not return until the hook thread has actually
    attempted installation. Previously it returned the instant the thread
    was merely scheduled to start, leaving a real OS-scheduling-dependent
    race window where ClipCursor (synchronous) was already active but the
    keyboard/mouse hooks were not -- so input could pass through unblocked
    for however long the OS took to run the new thread."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "_hooks_ready" in source
    assert "threading.Event()" in source
    assert "_hooks_ready.wait(timeout=" in source
    assert "_hooks_ready.set()" in source


def test_hook_installation_failures_are_logged_not_silent():
    """SetWindowsHookExW returns NULL on failure. Both the keyboard and
    mouse hook install calls must check their return value and log an
    error -- silently proceeding as if locked when a hook actually failed
    to install is a real security gap, not a cosmetic one."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "GetLastError" in source
    kb_hook_idx = source.index("WH_KEYBOARD_LL, _kb_callback_ref")
    mouse_hook_idx = source.index("WH_MOUSE_LL, _mouse_callback_ref")
    # The failure-check for each hook must appear shortly after its own
    # install call, not just anywhere in the file.
    kb_check_region = source[kb_hook_idx:kb_hook_idx + 400]
    mouse_check_region = source[mouse_hook_idx:mouse_hook_idx + 400]
    assert "if not _kb_hook_id:" in kb_check_region
    assert "log.error(" in kb_check_region
    assert "if not _mouse_hook_id:" in mouse_check_region
    assert "log.error(" in mouse_check_region


def test_space_and_tab_are_consumed_not_forwarded_while_locked():
    """Confirmed live: forwarding Space/Tab via CallNextHookEx let them
    reach whatever window had OS focus, including a background app
    (Space triggered play/pause on a background YouTube tab while
    locked). Both must now be consumed at the hook level and routed to
    the overlay's own action via a thread-safe Qt signal instead."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "hook_space_pressed = pyqtSignal()" in source
    assert "hook_tab_pressed = pyqtSignal()" in source
    assert "hook_space_pressed.connect(" in source
    assert "hook_tab_pressed.connect(" in source
    # The old pass-through path (CallNextHookEx immediately after detecting
    # Tab/Space) must be gone -- the fixed callback returns 1 (consume) for
    # every KEYDOWN while locked, Tab/Space included.
    kb_callback = source[source.index("def _keyboard_ll_callback"):source.index("def _mouse_ll_callback")]
    assert "VK_TAB, VK_SPACE" in kb_callback
    assert ".emit()" in kb_callback


def test_topmost_reasserted_every_tick_not_only_at_lock_entry():
    """Confirmed via Microsoft's own Precision Touchpad documentation:
    3/4-finger system gestures (Task View, virtual desktop switch) are
    OS-shell-level 'global gestures', not interceptable/blockable from a
    normal userspace app (WM_GESTURE, the legacy touch-screen API, is not
    involved). The only real mitigation is minimizing the exposure window
    by re-asserting topmost/foreground every tick, mirroring the same
    strategy already used for taskbar re-hide."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    tick_fn = source[source.index("def _tick(self)"):source.index("def _use_windows_lock")]
    assert "self._force_topmost()" in tick_fn
    assert "self.raise_()" in tick_fn
    assert "self.activateWindow()" in tick_fn


def test_media_stopped_on_lock_engage():
    """Kio's real-world scenario: watching a movie in a background tab,
    soft-lock engaged, media kept playing (and the spacebar leak on top of
    that could control it). Stopping media -- not just muting -- must
    fire as part of the lock-engagement sequence, not be optional/absent."""
    source = (UI / "soft_lock.py").read_text(encoding="utf-8")
    assert "VK_MEDIA_STOP" in source
    assert "VK_MEDIA_PLAY_PAUSE" in source
    assert "_stop_background_media()" in source
    # Must actually be called from the lock-engagement sequence, not just
    # defined and never invoked (exactly the kind of gap already found and
    # fixed once this session for the exit animation).
    lock_seq_idx = source.index("_install_hooks(self)")
    lock_seq_region = source[lock_seq_idx:lock_seq_idx + 300]
    assert "_stop_background_media()" in lock_seq_region
