import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

import app.control_center as control_center_module
from app.control_center import ControlCenter
from app.runtime import RuntimeStatus


class FakeSupervisor:
    def __init__(self, status, enrollment_present=True):
        self._status = status
        self._enrollment_present = enrollment_present
        self.start_calls = 0
        self.stop_calls = 0
        self.enrollment_calls = 0

    def status(self):
        return self._status

    def start(self):
        self.start_calls += 1
        self._status = RuntimeStatus.RUNNING
        return True

    def stop(self):
        self.stop_calls += 1
        self._status = RuntimeStatus.STOPPED

    def pause(self):
        self.stop_calls += 1
        self._status = RuntimeStatus.PAUSED

    def resume(self):
        self.start_calls += 1
        self._status = RuntimeStatus.RUNNING
        return True

    def set_idle_timeout(self, seconds):
        self.idle_timeout = seconds
        return True

    def enrollment_present(self):
        return self._enrollment_present

    def start_enrollment(self):
        self.enrollment_calls += 1
        return True


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_control_center_start_action_starts_stopped_supervisor(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.start_protection()

    assert supervisor.start_calls == 1
    assert view.status_text() == "Protection running"
    view.close()


def test_waiting_for_pause_keeps_event_loop_alive_and_blocks_duplicate_commands(qapp, tmp_path):
    import threading
    from PyQt6.QtCore import QTimer
    from PyQt6.QtTest import QTest
    entered, release = threading.Event(), threading.Event()
    class SlowPause(FakeSupervisor):
        def pause(self):
            entered.set()
            assert release.wait(3)
            super().pause()
            return True

    sup = SlowPause(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json", async_actions=True)
    callbacks = []
    try:
        view._run_primary_action()
        assert entered.wait(2)
        QTimer.singleShot(0, lambda: callbacks.append("paintable"))
        QTest.qWait(40)
        view._run_primary_action()
        view.quit_application()
        view._quick_timeout(120)
        view.show_page(5)
        assert callbacks == ["paintable"] and sup.stop_calls == 0
        assert "Pausing" in view._detail_label.text()
        assert not view._quick.primary.isEnabled()
        assert not hasattr(sup, "idle_timeout")
    finally:
        release.set()
    view._operation[0].result(timeout=3)
    view._complete_operation()
    assert sup.stop_calls == 1 and view._quick.primary.text() == "Resume protection"
    assert view._quick.primary.isEnabled()


def test_control_center_never_labels_a_dead_daemon_as_protected(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.DEGRADED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.refresh()

    assert view.status_text() == "Protection degraded"
    assert "protected" not in view.status_text().lower()
    view.close()


def test_control_center_guides_missing_enrollment_to_enrollment_action(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED, enrollment_present=False)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    assert view.primary_action_text() == "Enroll face"
    view._run_primary_action()
    assert supervisor.enrollment_calls == 1
    view.close()


def test_control_center_resume_action_recovers_a_paused_supervisor(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    assert view.primary_action_text() == "Resume protection"
    view._run_primary_action()

    assert supervisor.start_calls == 1
    assert view.status_text() == "Protection running"
    view.close()


def test_control_center_saves_bounded_idle_timeout_for_next_start(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")
    view._timeout_input.setValue(180)

    view.save_idle_timeout()

    assert supervisor.idle_timeout == 180
    assert "next start" in view.timeout_note_text().lower()
    view.close()


def test_control_center_opens_only_the_local_diagnostics_directory(qapp, tmp_path, monkeypatch):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED)
    diagnostics_path = tmp_path / "diagnostics"
    supervisor.open_log_directory = lambda: diagnostics_path
    opened = []
    monkeypatch.setattr(
        control_center_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.open_diagnostics()

    assert opened == [QUrl.fromLocalFile(str(diagnostics_path))]
    view.close()


def test_control_center_reenrollment_action_uses_guarded_supervisor(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.start_reenrollment()

    assert supervisor.enrollment_calls == 1
    view.close()

def test_control_center_timeout_preset_updates_spinbox_and_saves(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.apply_timeout_preset(120)

    assert view._timeout_input.value() == 120
    assert supervisor.idle_timeout == 120
    view.close()


def test_perf_chip_reads_only_the_log_tail(qapp, tmp_path):
    log_file = tmp_path / "daemon.log"
    junk = ("x" * 99 + "\n") * 5000  # ~500 KB of unrelated lines
    log_file.write_text(
        "Cold start complete: total=9.99s\n" + junk + "Cold start complete: total=1.23s\nCold start complete: total=",
        encoding="utf-8",
    )
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")

    view.refresh()

    assert view._perf_chip.text() == "1.23s"
    view.close()


def test_perf_chip_ignores_a_cold_start_line_outside_the_tail(qapp, tmp_path):
    log_file = tmp_path / "daemon.log"
    log_file.write_text(
        "Cold start complete: total=9.99s\n" + ("y" * 99 + "\n") * 5000,
        encoding="utf-8",
    )
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")

    view.refresh()

    assert view._perf_chip.text() == "Not measured"
    view.close()


def test_perf_chip_displays_current_daemon_millisecond_format(qapp, tmp_path):
    (tmp_path / "daemon.log").write_text("Cold start complete: model=830ms warmup=200ms camera=90ms total=1030ms\n")
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    assert view._perf_chip.text() == "1.03s"
    view.close()


class LockedSupervisor(FakeSupervisor):
    def controls_locked(self):
        return True


def test_control_center_disables_every_control_while_locked(qapp, tmp_path):
    supervisor = LockedSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.refresh()

    assert not view._primary_button.isEnabled()
    assert not view._reenroll_btn.isEnabled()
    assert not view._startover_btn.isEnabled()
    assert not view._timeout_input.isEnabled()
    assert not view._save_timeout_button.isEnabled()
    assert view._preset_buttons and not any(b.isEnabled() for b in view._preset_buttons)
    assert "locked" in view._detail_label.text().lower()
    view.close()


def test_control_center_actions_are_noops_while_locked(qapp, tmp_path):
    supervisor = LockedSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view._run_primary_action()
    view.start_reenrollment()
    view.apply_timeout_preset(300)
    view.save_idle_timeout()
    view.quit_application()

    assert supervisor.stop_calls == 0
    assert supervisor.enrollment_calls == 0
    assert not hasattr(supervisor, "idle_timeout")
    assert view._quitting is False
    view.close()


def test_current_timeout_preset_is_highlighted_and_follows_changes(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")

    def current():
        return [b.property("seconds") for b in view._preset_buttons if b.property("current") == "true"]

    view._timeout_input.setValue(120)
    view.save_idle_timeout()
    assert current() == [120]

    view.apply_timeout_preset(300)
    assert current() == [300]

    view._timeout_input.setValue(77)  # not a preset -> nothing highlighted
    view.save_idle_timeout()
    assert current() == []
    view.close()


# ---------------------------------------------------------------- tabbed UI
import json
import time


class ToggleSupervisor(FakeSupervisor):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.windows_lock = False
        self.refuse = False

    def windows_lock_enabled(self):
        return self.windows_lock

    def set_windows_lock_enabled(self, enabled):
        if self.refuse:
            return False
        self.windows_lock = enabled
        return True


def test_recovery_note_and_reset_are_wired_to_supervisor(qapp, tmp_path):
    class RecoverySupervisor(FakeSupervisor):
        maintained = 0
        reset_calls = 0
        locked = False

        def maintain(self):
            self.maintained += 1

        def recovery_note(self):
            return "Protection restarted automatically."

        def start_enrollment(self, *, start_over=False):
            self.reset_calls += int(start_over)
            return True

        def controls_locked(self):
            return self.locked

    sup = RecoverySupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    before = sup.maintained
    view.refresh()
    assert sup.maintained == before + 1
    assert view._detail_label.text() == sup.recovery_note()
    view._startover_btn.click()
    assert sup.reset_calls == 1
    sup.locked = True
    view.start_over_enrollment()
    assert sup.reset_calls == 1
    view.close()


def test_enrollment_page_shows_writer_result(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view.show()
    view.show_page(3)
    assert not view._enroll_result.isVisible()
    (tmp_path / "enroll_result.json").write_text(
        json.dumps({"outcome": "reset_not_authorized", "ts": time.time()}), encoding="utf-8"
    )
    view.refresh()
    assert view._enroll_result.isVisible()
    assert "did not confirm this account" in view._enroll_result.text()
    view.close()


def test_tabs_switch_by_click_and_shortcut_index(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    titles = ["Overview", "Protection", "Activity", "Enrollment", "Appearance", "Diagnostics"]
    assert [b.text() for b in view._nav] == titles
    for index, title in enumerate(titles):
        view._nav[index].click()
        assert view.current_page() == index
        assert view._page_title.text() == title
        assert view._nav[index].isChecked()
    view.show_page(99)                      # out of range clamps, never raises
    assert view.current_page() == len(titles) - 1
    view.show_page(-4)
    assert view.current_page() == 0
    view.close()


def test_top_tabs_preserve_names_badges_and_page_width_at_both_sizes(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING, enrollment_present=False), tmp_path / "settings.json")
    view.resize(720, 510)
    view.show()
    qapp.processEvents()
    assert view._tab_bar.width() == view.width()
    assert all(btn.compact for btn in view._nav)
    assert len({btn.y() for btn in view._nav}) == 1
    assert view._tab_bar.geometry().bottom() < view._stack.geometry().top()
    for index, btn in enumerate(view._nav):
        assert btn.accessibleName() == btn.text()
        btn.click()
        qapp.processEvents()
        assert view.current_page() == index
        assert view._stack.currentWidget().horizontalScrollBar().maximum() == 0, btn.text()
    assert view._nav[3].accessibleDescription() and view._nav[3].badge()
    view.resize(960, 680)
    qapp.processEvents()
    assert not any(btn.compact for btn in view._nav) and view._nav[-1].isChecked()
    view.close()


def test_toggle_respects_reduced_motion_and_does_not_snap_on_poll(qapp, monkeypatch):
    from app.cc_widgets import Toggle
    switch = Toggle()
    switch.show()
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    switch.click()
    assert switch.position == 1 and not switch._animation.state().value
    monkeypatch.delenv("MG_UI_REDUCE_MOTION")
    switch.click()
    switch._animation.setCurrentTime(30)
    before = switch.position
    assert 0 < before < 1
    switch.setChecked(False)
    assert switch.position == before
    switch.hide()
    assert switch.position == 0 and not switch._animation.state().value


def test_theme_survives_reopen_without_writing_protection_settings(qapp, tmp_path):
    path = tmp_path / "settings.json"
    supervisor = ToggleSupervisor(RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, path)
    next(b for b in view._theme_group.buttons() if b.text() == "Daylight").click()
    assert view.property("ccTheme") == "daylight"
    view.close()
    reopened = ControlCenter(supervisor, path)
    assert reopened._theme_group.checkedButton().text() == "Daylight"
    assert not path.exists()
    reopened.close()


def test_lock_status_error_disables_primary_action(qapp, tmp_path):
    class Unavailable(FakeSupervisor):
        def controls_locked(self):
            raise OSError("disconnected")

    sup = Unavailable(RuntimeStatus.STOPPED)
    view = ControlCenter(sup, tmp_path / "settings.json")
    assert not view._primary_button.isEnabled()
    view._run_primary_action()
    assert sup.start_calls == 0
    view.close()


def test_protection_keeps_settings_and_about_owns_the_overlay_guide(qapp, tmp_path):
    from PyQt6.QtWidgets import QLabel

    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view.show_page(1)
    assert not any(label.text() == "Privacy overlay"
                   for label in view._stack.currentWidget().findChildren(QLabel))
    assert not hasattr(view, "_windows_lock_toggle")
    view._info_button.click()
    dialog = view._about_dialog
    assert {"Privacy overlay", "Space", "Tab twice", "Win + L"} <= {
        label.text() for label in dialog.findChildren(QLabel)
    }
    dialog.accept()
    view.close()


def test_info_button_opens_and_closes_about_without_changing_tabs(qapp, tmp_path):
    from PyQt6.QtWidgets import QLabel

    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view.show_page(2)
    view._info_button.click()
    dialog = view._about_dialog
    assert dialog.isVisible()
    assert dialog.windowTitle() == "About MajestyGuard"
    assert view.current_page() == 2
    from app.cc_theme import theme_for
    assert theme_for(dialog) == theme_for(view)
    copy = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "not the Windows lock screen" in copy
    assert "Tab twice" in copy
    dialog.accept()
    assert view._about_dialog is None
    view.close()


def test_touchpad_settings_button_opens_windows_settings_only_on_click(qapp, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(control_center_module.QDesktopServices, "openUrl",
                        lambda url: calls.append(url.toString()) or True)
    view = ControlCenter(FakeSupervisor(RuntimeStatus.PAUSED), tmp_path / "settings.json")
    view._info_button.click()
    assert calls == []
    view._touchpad_settings_button.click()
    assert calls == ["ms-settings:devices-touchpad"]
    view._about_dialog.accept()
    view.close()


def test_about_dismisses_on_lock_and_settings_refuses_a_stale_click(qapp, tmp_path, monkeypatch):
    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    sup.controls_locked = lambda: False
    opened = []
    monkeypatch.setattr(control_center_module.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or False)
    view = ControlCenter(sup, tmp_path / "settings.json")
    view._info_button.click()
    original_timeout_note = view.timeout_note_text()
    view._touchpad_settings_button.click()
    assert "Bluetooth & devices" in view._touchpad_note.text()
    assert view.timeout_note_text() == original_timeout_note
    sup.controls_locked = lambda: True
    view._touchpad_settings_button.click()
    assert len(opened) == 1
    view.refresh()
    assert view._about_dialog is None and not view._info_button.isEnabled()
    view.close()


def test_copy_diagnostics_shares_status_and_metrics_without_raw_logs(qapp, tmp_path, monkeypatch):
    from app.diagnostics import ProcessReading

    clipboard = []
    monkeypatch.setattr(QApplication, "clipboard", lambda: type(
        "Clipboard", (), {"setText": lambda self, text: clipboard.append(text)}
    )())
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    sup.diagnostic_processes = lambda: {"Control Center": [42], "Protection": []}
    (tmp_path / "daemon.log").write_text("private local diagnostic line\n", encoding="utf-8")
    view = ControlCenter(sup, tmp_path / "settings.json")
    view._resource_sampler.sample = lambda _: (ProcessReading("Control Center", 42, 24 * 1024 ** 2, 0.1, 60),)
    view._nav[-1].click()
    assert clipboard == []
    view._copy_diagnostics_button.click()
    assert len(clipboard) == 1
    assert "Protection paused" in clipboard[0]
    assert "Worker RAM: 0 MiB" in clipboard[0] and "App RAM: 24.0 MiB" in clipboard[0]
    assert "private local diagnostic line" not in clipboard[0] and str(tmp_path) not in clipboard[0]
    assert "PID" not in clipboard[0]
    view.close()


def finish_history(view):
    for _ in range(3):
        if view._history_pending is None:
            return
        view._history_pending[1].result(timeout=3)
        view._refresh_history(view._state_dir() / "audit.log")
    assert view._history_pending is None


def test_busy_history_read_coalesces_and_does_not_block_navigation(qapp, tmp_path, monkeypatch):
    import threading
    from app import activity
    from daemon.audit_log import AuditLog
    log = AuditLog(tmp_path / "audit.log")
    log.record("daemon_start")
    gate = threading.Event()
    calls = []
    real = activity.read_history_snapshot
    def delayed(path, check):
        calls.append((threading.get_ident(), check))
        assert gate.wait(timeout=3)
        return real(path, check)
    monkeypatch.setattr(activity, "read_history_snapshot", delayed)
    try:
        view = ControlCenter(FakeSupervisor(RuntimeStatus.PAUSED), tmp_path / "settings.json")
        first = view._history_pending
        log.record("unlock_face")
        view.show_page(2)
        view.refresh()
        assert view._history_pending is first
        assert "checking" in view._integrity_title.text().lower()
    finally:
        gate.set()
    finish_history(view)
    assert all(thread != threading.get_ident() for thread, _ in calls)
    assert [check for _, check in calls] == [False, True]
    assert "consistent" in view._integrity_title.text().lower()
    assert view._stat_last.text() == "just now"


def test_activity_tab_shows_history_and_flags_tampering(qapp, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "daemon"))
    import audit_log
    log = audit_log.AuditLog(tmp_path / "audit.log")
    log.record("soft_lock", reason="idle")
    log.record("unlock_face")
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    finish_history(view)
    assert view._stat_last.text() == "just now"
    assert view._history_pending is None
    view.show_page(2)
    finish_history(view)
    assert "consistent" in view._integrity_title.text().lower()
    path = tmp_path / "audit.log"
    path.write_text(path.read_text().replace('"idle"', '"away"'))
    view.refresh()
    finish_history(view)
    assert "altered" in view._integrity_title.text().lower()
    view.close()


def test_activity_tab_shows_camera_and_protection_events_together(qapp, tmp_path):
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "degraded", "headline": "Camera view is dark", "hint": "Uncover the lens",
        "last_backend": "dshow", "recoveries": 1, "next_retry_in_s": 3, "updated_at": time.time(), "events": ["x"],
    }))
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view.show_page(2)
    page = view._stack.currentWidget().widget()
    assert page.isAncestorOf(view._activity_list) and page.isAncestorOf(view._camera_headline)
    assert view._camera_headline.text() == "Camera view is dark"
    assert view._stat_camera.text() == "Degraded"
    assert view._nav[2].badge() == "!"
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "healthy", "headline": "", "hint": "", "last_backend": "dshow", "updated_at": time.time(),
    }))
    view.refresh()
    assert view._stat_camera.text() == "Healthy" and view._nav[2].badge() == ""
    view.close()


def test_stale_camera_data_is_not_presented_as_live(qapp, tmp_path):
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "healthy", "last_backend": "dshow", "updated_at": time.time() - 3600,
    }))
    view = ControlCenter(FakeSupervisor(RuntimeStatus.STOPPED), tmp_path / "settings.json")
    assert view._camera_headline.text() == "No live camera data"
    assert view._stat_camera.text() == "Idle"
    view.close()


def test_paused_protection_never_shows_a_recent_camera_snapshot_as_live(qapp, tmp_path):
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "healthy", "last_backend": "dshow", "updated_at": time.time(),
    }))
    view = ControlCenter(FakeSupervisor(RuntimeStatus.PAUSED), tmp_path / "settings.json")
    assert view._camera_headline.text() == "No live camera data"
    view.close()


def test_quit_refuses_to_hide_tray_if_protection_could_not_stop(qapp, tmp_path, monkeypatch):
    supervisor = FakeSupervisor(RuntimeStatus.RUNNING)
    monkeypatch.setattr(supervisor, "stop", lambda: False)
    view = ControlCenter(supervisor, tmp_path / "settings.json")
    exited = []
    monkeypatch.setattr(qapp, "quit", lambda: exited.append(True))
    view.quit_application()
    assert not exited
    assert not view._quitting
    assert view._refresh_timer.isActive()
    view.close()


def test_diagnostics_distinguishes_worker_ram_from_control_center(qapp, tmp_path):
    from app.diagnostics import ProcessReading

    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    sup.diagnostic_processes = lambda: {"Control Center": [42], "Protection": [84]}
    view = ControlCenter(sup, tmp_path / "settings.json")
    mib = 1024 ** 2
    ui = ProcessReading("Control Center", 42, 24 * mib, 0.1, 60)
    worker = ProcessReading("Protection", 84, 64 * mib, 1.5, 12)
    view._resource_sampler.sample = lambda _groups: (ui, worker)
    view._nav[-1].click()
    assert view._worker_ram.text() == "64.0 MiB"
    assert view._center_ram.text() == "24.0 MiB"
    assert "84" in view._process_detail.text()
    view._resource_sampler.sample = lambda _groups: (ui,)
    view._primary_button.click()
    assert sup.stop_calls == 1
    assert view._worker_ram.text() == "0 MiB"
    assert "84" not in view._process_detail.text()
    view.close()


def test_live_camera_release_is_shown_as_parked(qapp, tmp_path):
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "healthy", "last_backend": "dshow", "capture_open": False, "updated_at": time.time(),
    }))
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    assert view._stat_camera.text() == "Parked"
    assert view._camera_headline.text() == "Camera parked"
    view.close()


def test_diagnostics_reports_latest_measured_verify_and_camera_open(qapp, tmp_path):
    (tmp_path / "daemon.log").write_text(
        "[VerifyTiming] outcome=owner_verified camera_ready_ms=1891 first_result_ms=2188 decision_ms=2656\n",
        encoding="utf-8",
    )
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view._nav[-1].click()
    assert view._verify_time.text() == "2.66 s"
    assert view._camera_open_time.text() == "1.89 s"
    view.close()


def test_diagnostics_stop_button_stops_degraded_worker(qapp, tmp_path):
    supervisor = FakeSupervisor(RuntimeStatus.DEGRADED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")
    view._nav[-1].click()
    view._stop_button.click()
    assert supervisor.stop_calls == 1
    assert view.status_text() == "Protection stopped"
    assert not view._stop_button.isEnabled()
    view.close()


def test_missing_enrollment_badges_the_enrollment_tab(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.STOPPED, enrollment_present=False), tmp_path / "settings.json")
    assert view._nav[3].badge() == "!"
    assert view._reenroll_btn.text() == "Enroll face"
    assert view._side_pill.text() == "Enroll to start"
    view.close()


def test_timeout_stat_follows_a_saved_change(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    view.apply_timeout_preset(300)
    assert view._stat_timeout.text() == "5 min"
    view.apply_timeout_preset(90)
    assert view._stat_timeout.text() == "1 min 30 s"
    view.close()


def test_quick_controls_share_primary_state_and_reject_stale_actions(qapp, tmp_path):
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(sup, tmp_path / "settings.json")
    panel = view._quick
    assert panel.primary.text() == "Resume protection"
    panel.primary.click()
    assert sup.start_calls == 1 and panel.primary.text() == "Pause protection"
    sup._status = RuntimeStatus.STOPPED
    panel.primary.click()
    assert sup.start_calls == 1 and sup.stop_calls == 0  # stale Pause must never start it
    assert panel.primary.text() == "Start protection"
    sup.controls_locked = lambda: True
    panel.primary.click()
    assert sup.start_calls == 1
    view.refresh()
    assert not panel.primary.isEnabled() and not view._quick_button.isEnabled()


def test_quick_routes_work_and_locked_session_does_not_raise_center(qapp, tmp_path):
    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    for key, index in (("activity", 2), ("protection", 1), ("diagnostics", 5)):
        view._quick.routes[key].click()
        assert view.current_page() == index and view.isVisible()
    view.hide()
    sup.controls_locked = lambda: True
    view._quick.open_center.click()
    assert not view.isVisible() and view.current_page() == 5


def test_tray_position_uses_logical_coordinates_of_taskbar_monitor():
    from PyQt6.QtCore import QRect, QSize
    from app.quick_controls import popup_position
    work = QRect(-1920, 40, 1920, 1040)
    for anchor in (QRect(-152, 0, 20, 20), QRect(-9, 1079, 18, 18), QRect(-1921, 500, 18, 18)):
        size = QSize(340, 380)
        assert work.contains(QRect(popup_position(anchor, work, size), size))


def test_quick_panel_dismisses_on_escape_and_lock_without_mutating_protection(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    view._quick_from_window()
    assert view._quick.isVisible() and not view.isVisible()
    assert not view._quick._entry.state().value
    QTest.keyClick(view._quick, Qt.Key.Key_Escape)
    assert not view._quick.isVisible()
    view._quick_from_window()
    sup.controls_locked = lambda: True
    view.refresh()
    assert not view._quick.isVisible() and sup.stop_calls == 0


def test_activity_filter_uses_cached_recent_events_and_survives_refresh(qapp, tmp_path):
    from daemon.audit_log import AuditLog
    log = AuditLog(tmp_path / "audit.log")
    log.record("unlock_face")
    log.record("camera_fault", fault="black_frames")
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    finish_history(view)
    view._activity_search.setText("LENS")
    assert len(view._activity_list._times) == 1
    view._alerts_only.click()
    view.refresh()
    assert len(view._activity_list._times) == 1
    assert len(view._recent_list._times) == 2
    view._activity_search.setText("unlocked")
    assert not view._activity_list._times
    view._alerts_only.click()
    assert len(view._activity_list._times) == 1


def test_memory_trace_expires_old_readings_and_preserves_unknowns(qapp):
    from app.cc_widgets import MemoryTrace
    trace = MemoryTrace(None)
    for n in range(80):
        trace.observe(n, now=n)
    trace.observe(None, now=79.1)
    assert len(trace._points) <= 61 and trace._points[-1][1] is None
    trace.observe(0, now=200)
    assert list(trace._points) == [(200, 0)]


def test_page_transition_drops_old_effect_and_honors_reduced_motion(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    view = ControlCenter(FakeSupervisor(RuntimeStatus.PAUSED), tmp_path / "settings.json")
    view.show()
    view.show_page(1)
    old = view._stack.currentWidget()
    assert old.graphicsEffect() is None
    motion = view._tab_bar._motion
    assert motion.state().value
    motion.setCurrentTime(60)
    intermediate = view._tab_bar._line.geometry()
    view.show_page(2)
    assert motion.startValue() == intermediate
    motion.setCurrentTime(motion.duration())
    assert view._nav[2].geometry().contains(view._tab_bar._line.geometry())
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    view.show_page(0)
    assert not motion.state().value
    view.hide()


def test_shutter_uses_only_explicit_live_capture_readings(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UI_REDUCE_MOTION", "1")
    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    for field, expected in (({}, "Not reported"), ({"capture_open": True}, "In use"),
                            ({"capture_open": False}, "Parked")):
        (tmp_path / "camera_health.json").write_text(json.dumps(
            {"updated_at": time.time(), "status": "healthy", **field}))
        view.refresh()
        assert view._camera_caption.text() == expected
        for aperture in (view._aperture, view._quick.aperture):
            assert aperture.opening == float(expected == "In use")
            assert not aperture._travel.state().value


def test_overview_plays_supplied_reel_only_while_visible_and_active(qapp, tmp_path, monkeypatch):
    from PyQt6.QtGui import QMovie
    from PyQt6.QtTest import QTest

    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(sup, tmp_path / "settings.json")
    view.show()
    art = view._aperture
    try:
        assert art._movie is None
        for status in (RuntimeStatus.STARTING, RuntimeStatus.RUNNING):
            before = art._movie.currentFrameNumber() if art._movie else None
            sup._status = status
            view.refresh()
            assert art._movie.isValid() and art._movie.frameCount() == 204
            assert art._movie.state() == QMovie.MovieState.Running
            assert art._movie.speed() == 100
            assert view._camera_caption.text() == "Not reported"
            assert art.opening == 0
            assert view._quick.aperture._movie is None
            if before is None:
                QTest.qWait(150)
            else:
                assert before > 0 and art._movie.currentFrameNumber() == before
        first = art._movie.currentFrameNumber()
        view.refresh()
        assert art._movie.currentFrameNumber() == first
        QTest.qWait(160)
        assert art._movie.currentFrameNumber() > first

        view.show_page(1)
        assert art._movie.state() == QMovie.MovieState.NotRunning
        view.show_page(0)
        assert art._movie.state() == QMovie.MovieState.Running
        assert art._movie.frameCount() == 72 and art._movie.speed() == 75
        view._set_reduced_motion(True)
        assert art._movie.state() == QMovie.MovieState.NotRunning
        assert art._art_opacity == 1 and not art._still.isNull()
        view._set_reduced_motion(False)
        assert art._movie.state() == QMovie.MovieState.Running
        sup._status = RuntimeStatus.PAUSED
        view.refresh()
        assert art._movie.state() == QMovie.MovieState.NotRunning
        assert art._closing and not art._last_image.isNull()
        art._fade.setCurrentTime(art._fade.duration())
        assert art._art_opacity == 0
    finally:
        view.hide()


def test_worker_starting_can_be_paused_and_never_implies_ready(qapp, tmp_path):
    sup = FakeSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    for phase, title in (("loading", "Starting protection"), ("unconfirmed", "Startup not confirmed"),
                         ("profile-unavailable", "Face profile unavailable"), ("ready", "Protection running")):
        sup.startup_reading = lambda: phase
        view.refresh()
        assert view.status_text() == title
        assert view._primary_button.isEnabled() and view.primary_action_text() == "Pause protection"

def test_resume_animates_while_worker_is_pending_then_recovers_on_failure(qapp, tmp_path, monkeypatch):
    import threading
    from PyQt6.QtGui import QMovie

    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    release = threading.Event()
    sup = FakeSupervisor(RuntimeStatus.PAUSED)

    def resume():
        release.wait(3)
        return False

    sup.resume = resume
    view = ControlCenter(sup, tmp_path / "settings.json", async_actions=True)
    view.show()
    try:
        view._primary_button.click()
        assert view._operation is not None
        assert view._aperture._movie.state() == QMovie.MovieState.Running
        release.set()
        view._operation[0].result(timeout=3)
        view._complete_operation()
        assert view._aperture._movie.state() == QMovie.MovieState.NotRunning
        assert view.status_text() == "Protection paused"
    finally:
        release.set()
        view.hide()


def test_transport_icon_morphs_on_state_change_not_poll(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(sup, tmp_path / "settings.json")
    view.show()
    button = view._primary_button
    button.click()
    button._tween.setCurrentTime(55)
    phase = button.transportPhase
    assert 0 < phase < 1
    view.refresh()
    assert button.transportPhase == phase
    view.hide()
    assert not button._tween.state().value


def test_tray_timeout_persists_only_confirmed_values_and_rechecks_lock(qapp, tmp_path):
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(sup, tmp_path / "settings.json")
    view._quick.presets[120].click()
    assert sup.idle_timeout == 120 and view._quick.readings["protection"].text() == "2 min"
    assert view._quick.presets[120].isChecked()
    sup.set_idle_timeout = lambda _: False
    view._quick.presets[300].click()
    assert view._quick.presets[120].isChecked() and not view._quick.presets[300].isChecked()
    assert "could not be saved" in view._quick.note.text()
    sup.controls_locked = lambda: True
    view._quick_timeout(30)
    assert sup.idle_timeout == 120
    assert not any(b.isEnabled() for b in view._quick.presets.values())


def test_appearance_motion_preference_applies_to_tray_and_survives_reopening(qapp, tmp_path, monkeypatch):
    from app.cc_theme import reduced_motion
    monkeypatch.delenv("MG_UI_REDUCE_MOTION", raising=False)
    path = tmp_path / "settings.json"
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    view = ControlCenter(sup, path)
    view._motion_switch.click()
    assert reduced_motion(view._quick.aperture)
    reopened = ControlCenter(sup, path)
    assert reopened._motion_switch.isChecked() and reduced_motion(reopened._aperture)
    assert not path.exists()


def test_background_poll_maintains_runtime_without_rebuilding_hidden_dashboard(qapp, tmp_path):
    sup = FakeSupervisor(RuntimeStatus.PAUSED)
    calls = []
    sup.maintain = lambda: calls.append("maintain")
    view = ControlCenter(sup, tmp_path / "settings.json")
    view._refresh_pages = lambda: calls.append("pages")
    calls.clear()
    view._refresh_timer.timeout.emit()
    assert calls == ["maintain"]
    view._refresh_timer.stop()
