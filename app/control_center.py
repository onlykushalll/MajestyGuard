"""A compact, local-only Control Center for MajestyGuard."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.runtime import RuntimeStatus


class SupervisorProtocol(Protocol):
    def status(self) -> RuntimeStatus: ...
    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def enrollment_present(self) -> bool: ...
    def start_enrollment(self) -> bool: ...
    def open_log_directory(self) -> Path: ...
    def pause(self) -> None: ...
    def resume(self) -> bool: ...
    def set_idle_timeout(self, seconds: int) -> bool: ...


_STATUS_COPY = {
    RuntimeStatus.STOPPED: ("Protection stopped", "Start protection", "#7f8b9c"),
    RuntimeStatus.STARTING: ("Starting protection", "Starting", "#237bff"),
    RuntimeStatus.RUNNING: ("Protection running", "Pause protection", "#16a36a"),
    RuntimeStatus.PAUSED: ("Protection paused", "Resume protection", "#7f8b9c"),
    RuntimeStatus.DEGRADED: ("Protection degraded", "Restart protection", "#c06a00"),
}


class ControlCenter(QMainWindow):
    """Lifecycle UI only; it never receives biometric or liveness data."""

    def __init__(self, supervisor: SupervisorProtocol, settings_path: Path) -> None:
        super().__init__()
        self._supervisor = supervisor
        self._settings_path = Path(settings_path)
        self._quitting = False
        self._build_window()
        self._build_tray()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    def _build_window(self) -> None:
        self.setWindowTitle("MajestyGuard")
        self.setMinimumSize(480, 340)
        self.resize(520, 380)
        self.setStyleSheet(
            "QMainWindow { background: #f5f7fa; color: #10141a; }"
            "QLabel#eyebrow { color: #607084; font: 600 10px 'Segoe UI Variable'; letter-spacing: 1px; }"
            "QLabel#title { color: #10141a; font: 600 26px 'Segoe UI Variable Display'; }"
            "QLabel#status { color: #10141a; font: 600 18px 'Segoe UI Variable Display'; }"
            "QLabel#detail { color: #607084; font: 400 12px 'Segoe UI Variable Text'; }"
            "QPushButton { background: #10141a; color: white; border: 0; border-radius: 6px;"
            " padding: 10px 16px; font: 600 12px 'Segoe UI Variable'; }"
            "QPushButton:hover { background: #26313e; }"
            "QPushButton:pressed { background: #0a0d11; }"
            "QPushButton:disabled { background: #d7dde5; color: #8a96a6; }"
        )

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 28, 30, 26)
        layout.setSpacing(0)

        eyebrow = QLabel("MAJESTYGUARD", root)
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)

        title = QLabel("Local protection", root)
        title.setObjectName("title")
        title.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(title)

        layout.addSpacing(26)
        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        self._rail = QFrame(root)
        self._rail.setFixedWidth(4)
        self._rail.setFixedHeight(82)
        status_row.addWidget(self._rail)

        status_copy = QVBoxLayout()
        status_copy.setSpacing(5)
        self._status_label = QLabel(root)
        self._status_label.setObjectName("status")
        status_copy.addWidget(self._status_label)
        self._detail_label = QLabel(
            "Camera and biometric decisions stay inside the local daemon.",
            root,
        )
        self._detail_label.setObjectName("detail")
        self._detail_label.setWordWrap(True)
        status_copy.addWidget(self._detail_label)
        status_row.addLayout(status_copy, 1)
        layout.addLayout(status_row)

        layout.addStretch(1)
        self._primary_button = QPushButton(root)
        self._primary_button.clicked.connect(self._run_primary_action)
        layout.addWidget(self._primary_button)

        layout.addSpacing(18)
        timeout_row = QHBoxLayout()
        timeout_row.setSpacing(10)
        timeout_label = QLabel("Lock after", root)
        timeout_label.setObjectName("detail")
        timeout_row.addWidget(timeout_label)
        self._timeout_input = QSpinBox(root)
        self._timeout_input.setRange(15, 1800)
        self._timeout_input.setSuffix(" seconds")
        self._timeout_input.setValue(self._current_idle_timeout())
        timeout_row.addWidget(self._timeout_input)
        timeout_row.addStretch(1)
        self._save_timeout_button = QPushButton("Save", root)
        self._save_timeout_button.clicked.connect(self.save_idle_timeout)
        timeout_row.addWidget(self._save_timeout_button)
        layout.addLayout(timeout_row)
        self._timeout_note = QLabel("Changes apply on the next start.", root)
        self._timeout_note.setObjectName("detail")
        self._timeout_note.setContentsMargins(0, 6, 0, 0)
        layout.addWidget(self._timeout_note)

        self._hint_label = QLabel("Windows sign-in remains protected by Windows.", root)
        self._hint_label.setObjectName("detail")
        self._hint_label.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(self._hint_label)

    def _build_tray(self) -> None:
        tray_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray = QSystemTrayIcon(tray_icon, self)
        self._tray.setToolTip("MajestyGuard")
        menu = QMenu(self)
        self._tray_primary_action = QAction(self)
        self._tray_primary_action.triggered.connect(self._run_primary_action)
        menu.addAction(self._tray_primary_action)

        show_action = QAction("Open Control Center", self)
        show_action.triggered.connect(self.show_center)
        menu.addAction(show_action)

        reenroll_action = QAction("Re-enroll face", self)
        reenroll_action.triggered.connect(self.start_reenrollment)
        menu.addAction(reenroll_action)

        diagnostics_action = QAction("Open diagnostics", self)
        diagnostics_action.triggered.connect(self.open_diagnostics)
        menu.addAction(diagnostics_action)
        menu.addSeparator()

        quit_action = QAction("Quit MajestyGuard", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._handle_tray_activation)

    def status_text(self) -> str:
        return self._status_label.text()

    def primary_action_text(self) -> str:
        return self._primary_button.text()

    def enrollment_present(self) -> bool:
        return self._supervisor.enrollment_present()

    def timeout_note_text(self) -> str:
        return self._timeout_note.text()

    def _current_idle_timeout(self) -> int:
        try:
            from app.config import load_settings

            return load_settings(self._settings_path).idle_timeout_seconds
        except OSError:
            return 60

    def refresh(self) -> None:
        enrolled = self._supervisor.enrollment_present()
        status = self._supervisor.status()
        if not enrolled:
            text, action, color = "Enrollment required", "Enroll face", "#c06a00"
            detail = "Create a local face profile before starting protection."
        else:
            text, action, color = _STATUS_COPY[status]
            detail = "Camera and biometric decisions stay inside the local daemon."

        self._status_label.setText(text)
        self._detail_label.setText(detail)
        self._primary_button.setText(action)
        self._primary_button.setEnabled(status is not RuntimeStatus.STARTING)
        self._tray_primary_action.setText(action)
        self._tray_primary_action.setEnabled(status is not RuntimeStatus.STARTING)
        self._rail.setStyleSheet(f"background: {color}; border-radius: 2px;")

    def start_protection(self) -> None:
        self._supervisor.start()
        self.refresh()

    def save_idle_timeout(self) -> None:
        seconds = self._timeout_input.value()
        if self._supervisor.set_idle_timeout(seconds):
            self._timeout_note.setText("Saved. Changes apply on the next start.")
        else:
            self._timeout_note.setText("That timeout is outside the permitted range.")

    def start_reenrollment(self) -> None:
        if self._supervisor.start_enrollment():
            self._timeout_note.setText("Enrollment opened in a separate window.")
        else:
            self._timeout_note.setText("Enrollment is already running or unavailable.")

    def open_diagnostics(self) -> None:
        directory = self._supervisor.open_log_directory()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _run_primary_action(self) -> None:
        if not self._supervisor.enrollment_present():
            enrollment = getattr(self._supervisor, "start_enrollment", None)
            if callable(enrollment):
                enrollment()
            self.refresh()
            return
        if self._supervisor.status() is RuntimeStatus.RUNNING:
            self._supervisor.pause()
        elif self._supervisor.status() is RuntimeStatus.PAUSED:
            self._supervisor.resume()
        else:
            self.start_protection()
        self.refresh()

    def show_center(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def show_tray(self) -> None:
        self._tray.show()

    def quit_application(self) -> None:
        self._quitting = True
        self._refresh_timer.stop()
        self._supervisor.stop()
        self._tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_center()
