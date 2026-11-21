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
        "Cold start complete: total=9.99s\n" + junk + "Cold start complete: total=1.23s\n",
        encoding="utf-8",
    )
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")

    view.refresh()

    assert view._perf_chip.text() == "Cold start: 1.23s"
    view.close()


def test_perf_chip_ignores_a_cold_start_line_outside_the_tail(qapp, tmp_path):
    log_file = tmp_path / "daemon.log"
    log_file.write_text(
        "Cold start complete: total=9.99s\n" + ("y" * 99 + "\n") * 5000,
        encoding="utf-8",
    )
    view = ControlCenter(FakeSupervisor(RuntimeStatus.RUNNING), tmp_path / "settings.json")

    view.refresh()

    assert view._perf_chip.text() == "Engine: Ready"
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
