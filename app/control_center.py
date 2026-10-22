"""A compact, local-only Obsidian Glass Control Center for MajestyGuard."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSpinBox,
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


def _create_tray_icon(accent_hex: str) -> QIcon:
    """Render a crisp 64x64 antialiased vector shield/orb icon for the system tray."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Outer dark obsidian squircle
    bg_path = QPainterPath()
    bg_path.addRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    painter.fillPath(bg_path, QColor("#0B0F17"))
    painter.setPen(QPen(QColor(accent_hex), 2.5))
    painter.drawPath(bg_path)

    # Inner glowing core + orbital dots
    accent = QColor(accent_hex)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(accent)
    painter.drawEllipse(QPointF(32, 32), 7.5, 7.5)

    ring_dot = QColor("#F2F4F8")
    ring_dot.setAlpha(210)
    painter.setBrush(ring_dot)
    for idx in range(6):
        angle = idx * 1.04719755
        painter.drawEllipse(
            QPointF(32 + 15.5 * (1.0 if idx % 2 == 0 else 0.92) * float(__import__("math").cos(angle)),
                    32 + 15.5 * (1.0 if idx % 2 == 0 else 0.92) * float(__import__("math").sin(angle))),
            2.4,
            2.4,
        )

    painter.end()
    return QIcon(pixmap)


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
        self.setMinimumSize(500, 420)
        self.resize(540, 450)
        self.setStyleSheet(
            "QMainWindow { background: #090C12; color: #F2F4F8; }"
            "QLabel#eyebrow { color: #64D2FF; font: 600 10px 'Segoe UI Variable Text'; letter-spacing: 1.4px; }"
            "QLabel#title { color: #F2F4F8; font: 600 26px 'Segoe UI Variable Display'; }"
            "QFrame#card { background: #101622; border: 1px solid #1E2738; border-radius: 12px; }"
            "QLabel#status { color: #F2F4F8; font: 600 18px 'Segoe UI Variable Display'; }"
            "QLabel#detail { color: #8A96A8; font: 400 12px 'Segoe UI Variable Text'; }"
            "QLabel#chip { background: #131B29; color: #C5D0E0; border: 1px solid #222E42;"
            " border-radius: 8px; padding: 4px 10px; font: 600 11px 'Segoe UI Variable Text'; }"
            "QPushButton { background: #F2F4F8; color: #090C12; border: 0; border-radius: 8px;"
            " padding: 10px 18px; font: 600 12px 'Segoe UI Variable Display'; }"
            "QPushButton:hover { background: #FFFFFF; }"
            "QPushButton:pressed { background: #D8E0EC; }"
            "QPushButton:disabled { background: #1A2232; color: #5B677A; }"
            "QPushButton#secondary { background: #131B29; color: #D5DCE6; border: 1px solid #243146; }"
            "QPushButton#secondary:hover { background: #1B2538; color: #FFFFFF; }"
            "QSpinBox { background: #101622; color: #F2F4F8; border: 1px solid #243146;"
            " border-radius: 8px; padding: 6px 10px; font: 600 12px 'Segoe UI Variable Text'; }"
        )

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(0)

        eyebrow = QLabel("MAJESTYGUARD CONTROL CENTER", root)
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)

        title = QLabel("Local protection", root)
        title.setObjectName("title")
        title.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(title)

        layout.addSpacing(18)
        card = QFrame(root)
        card.setObjectName("card")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(16)

        self._rail = QFrame(card)
        self._rail.setFixedWidth(4)
        self._rail.setFixedHeight(68)
        card_layout.addWidget(self._rail)

        status_copy = QVBoxLayout()
        status_copy.setSpacing(5)
        self._status_label = QLabel(card)
        self._status_label.setObjectName("status")
        status_copy.addWidget(self._status_label)
        self._detail_label = QLabel(
            "Camera and biometric decisions stay inside the local daemon.",
            card,
        )
        self._detail_label.setObjectName("detail")
        self._detail_label.setWordWrap(True)
        status_copy.addWidget(self._detail_label)
        card_layout.addLayout(status_copy, 1)
        layout.addWidget(card)

        layout.addSpacing(12)
        telemetry_row = QHBoxLayout()
        telemetry_row.setSpacing(8)
        self._gallery_chip = QLabel("Gallery: --", root)
        self._gallery_chip.setObjectName("chip")
        self._lock_chip = QLabel("Session: UNLOCKED", root)
        self._lock_chip.setObjectName("chip")
        telemetry_row.addWidget(self._gallery_chip)
        telemetry_row.addWidget(self._lock_chip)
        telemetry_row.addStretch(1)
        layout.addLayout(telemetry_row)

        layout.addStretch(1)
        self._primary_button = QPushButton(root)
        self._primary_button.clicked.connect(self._run_primary_action)
        layout.addWidget(self._primary_button)

        layout.addSpacing(10)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        self._reenroll_btn = QPushButton("Re-enroll face", root)
        self._reenroll_btn.setObjectName("secondary")
        self._reenroll_btn.clicked.connect(self.start_reenrollment)
        quick_row.addWidget(self._reenroll_btn)

        self._diag_btn = QPushButton("Open diagnostics", root)
        self._diag_btn.setObjectName("secondary")
        self._diag_btn.clicked.connect(self.open_diagnostics)
        quick_row.addWidget(self._diag_btn)
        layout.addLayout(quick_row)

        layout.addSpacing(16)
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
        self._save_timeout_button.setObjectName("secondary")
        self._save_timeout_button.clicked.connect(self.save_idle_timeout)
        timeout_row.addWidget(self._save_timeout_button)
        layout.addLayout(timeout_row)

        self._timeout_note = QLabel("Changes apply on the next start.", root)
        self._timeout_note.setObjectName("detail")
        self._timeout_note.setContentsMargins(0, 6, 0, 0)
        layout.addWidget(self._timeout_note)

        self._hint_label = QLabel("Windows sign-in remains protected by Windows.", root)
        self._hint_label.setObjectName("detail")
        self._hint_label.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self._hint_label)

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(_create_tray_icon("#7f8b9c"), self)
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

    def _refresh_telemetry_chips(self, enrolled: bool) -> None:
        state_dir = self._settings_path.parent
        if not enrolled:
            self._gallery_chip.setText("Gallery: Not enrolled")
        else:
            meta_file = state_dir / "embeddings_v2_meta.json"
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    count = meta.get("selected_count", "?")
                    profile = str(meta.get("profile", "robust")).capitalize()
                    self._gallery_chip.setText(f"Gallery: {count} templates · {profile}")
                except Exception:
                    self._gallery_chip.setText("Gallery: Enrolled (DPAPI)")
            else:
                self._gallery_chip.setText("Gallery: Enrolled (DPAPI)")

        lock_file = state_dir / "lock_state.txt"
        try:
            lock_state = lock_file.read_text(encoding="utf-8").strip() if lock_file.is_file() else "UNLOCKED"
        except OSError:
            lock_state = "UNLOCKED"
        self._lock_chip.setText(f"Session: {lock_state or 'UNLOCKED'}")

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
        self._tray.setIcon(_create_tray_icon(color))
        self._refresh_telemetry_chips(enrolled)

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
