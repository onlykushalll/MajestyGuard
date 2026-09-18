import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

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
