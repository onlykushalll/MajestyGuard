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


class LockedSupervisor(FakeSupervisor):
    def controls_locked(self):
        return True


def test_control_center_disables_every_control_while_locked(qapp, tmp_path):
    supervisor = LockedSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.refresh()

    assert not view._primary_button.isEnabled()
    assert not view._reenroll_btn.isEnabled()
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


def test_tabs_switch_by_click_and_shortcut_index(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    titles = ["Overview", "Protection", "Camera", "Activity", "Enrollment", "About", "Appearance"]
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


def test_windows_lock_switch_reads_and_writes_the_supervisor(qapp, tmp_path):
    sup = ToggleSupervisor(RuntimeStatus.RUNNING)
    view = ControlCenter(sup, tmp_path / "settings.json")
    assert view._windows_lock_toggle.isChecked() is False
    view._windows_lock_toggle.click()
    assert sup.windows_lock is True and view._windows_lock_toggle.isChecked()
    sup.refuse = True
    view._windows_lock_toggle.click()          # refused: the switch must snap back
    assert sup.windows_lock is True and view._windows_lock_toggle.isChecked()
    view.close()


def test_windows_lock_switch_is_disabled_while_locked_or_unsupported(qapp, tmp_path):
    class Locked(ToggleSupervisor):
        def controls_locked(self):
            return True

    locked = ControlCenter(Locked(RuntimeStatus.RUNNING), tmp_path / "a.json")
    assert not locked._windows_lock_toggle.isEnabled()
    locked.close()
    plain = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "b.json")
    assert not plain._windows_lock_toggle.isEnabled()
    plain.close()


def test_activity_tab_shows_history_and_flags_tampering(qapp, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "daemon"))
    import audit_log
    log = audit_log.AuditLog(tmp_path / "audit.log")
    log.record("soft_lock", reason="idle")
    log.record("unlock_face")
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
    assert view._stat_last.text() == "just now"
    assert "intact" in view._integrity_title.text().lower()
    path = tmp_path / "audit.log"
    path.write_text(path.read_text().replace('"idle"', '"away"'))
    view.refresh()
    assert "altered" in view._integrity_title.text().lower()
    view.close()


def test_camera_tab_reflects_a_covered_lens_and_badges_the_tab(qapp, tmp_path):
    (tmp_path / "camera_health.json").write_text(json.dumps({
        "status": "degraded", "headline": "Camera view is dark", "hint": "Uncover the lens",
        "last_backend": "dshow", "recoveries": 1, "next_retry_in_s": 3, "updated_at": time.time(), "events": ["x"],
    }))
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")
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


def test_missing_enrollment_badges_the_enrollment_tab(qapp, tmp_path):
    view = ControlCenter(FakeSupervisor(RuntimeStatus.STOPPED, enrollment_present=False), tmp_path / "settings.json")
    assert view._nav[4].badge() == "!"
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
