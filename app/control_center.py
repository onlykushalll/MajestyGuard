"""MajestyGuard Control Center: a local device dashboard.

Lifecycle and settings UI only. It never receives biometric or liveness data;
everything it shows about the camera and history comes from the small
non-biometric files the daemon writes (camera_health.json, audit.log).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import QPoint, QRect, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QCursor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QBoxLayout,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app import activity
from app.aperture import Aperture
from app.cc_theme import THEMES, reduced_motion, stylesheet
from app.cc_widgets import Dot, EventMarker, MemoryTrace, NavButton, Surface, TabStrip, Toggle, TransportButton, WorkspaceBackground, make_tray_icon
from app.diagnostics import ResourceSampler, read_verify_timing
from app.quick_controls import QuickControls
from app.runtime import RuntimeStatus
from ui.brand import BrandTrace


class SupervisorProtocol(Protocol):
    def status(self) -> RuntimeStatus: ...
    def start(self) -> bool: ...
    def stop(self) -> bool: ...
    def enrollment_present(self) -> bool: ...
    def start_enrollment(self) -> bool: ...
    def open_log_directory(self) -> Path: ...
    def pause(self) -> bool: ...
    def resume(self) -> bool: ...
    def set_idle_timeout(self, seconds: int) -> bool: ...


_LOCKED_NOTE = "Session is locked. Controls are disabled until you unlock."

_STATUS_COPY = {
    RuntimeStatus.STOPPED: ("Protection stopped", "Start protection", "#8590a3"),
    RuntimeStatus.STARTING: ("Starting protection", "Starting", "#0a84ff"),
    RuntimeStatus.RUNNING: ("Protection running", "Pause protection", "#1fa855"),
    RuntimeStatus.PAUSED: ("Protection paused", "Resume protection", "#8590a3"),
    RuntimeStatus.DEGRADED: ("Protection degraded", "Restart protection", "#d97706"),
}
_SIDE_PILL = {
    RuntimeStatus.STOPPED: ("Stopped", "idle"),
    RuntimeStatus.STARTING: ("Starting", "info"),
    RuntimeStatus.RUNNING: ("Protected", "ok"),
    RuntimeStatus.PAUSED: ("Paused", "idle"),
    RuntimeStatus.DEGRADED: ("Needs attention", "warn"),
}

_PAGES = (
    ("overview", "Overview", "Protection and activity on this device"),
    ("protection", "Protection", "How and when this PC locks"),
    ("activity", "Activity", "Camera health and recent protection events"),
    ("enrollment", "Enrollment", "Your face profile"),
    ("appearance", "Appearance", "Control Center theme"),
    ("diagnostics", "Diagnostics", "Live process and resource usage"),
)
_ACTIVITY_PAGE = 2
_ENROLLMENT_PAGE = 3
_HISTORY_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mg-history")
_LIFECYCLE_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mg-lifecycle")

def _palette_icon(p) -> QIcon:
    pix = QPixmap(112, 64)
    pix.fill(QColor(p.bg))
    painter = QPainter(pix)
    painter.fillRect(0, 0, 28, 64, QColor(p.rail))
    painter.fillRect(5, 9, 16, 4, QColor(p.muted))
    painter.fillRect(4, 22, 20, 8, QColor(p.selected))
    painter.fillRect(4, 22, 2, 8, QColor(p.accent))
    painter.fillRect(36, 10, 35, 4, QColor(p.text))
    painter.fillRect(36, 21, 68, 21, QColor(p.panel))
    painter.fillRect(36, 21, 2, 21, QColor(p.accent))
    painter.fillRect(43, 27, 31, 3, QColor(p.text))
    painter.fillRect(43, 34, 17, 3, QColor(p.accent))
    painter.fillRect(36, 47, 32, 9, QColor(p.raised))
    painter.fillRect(72, 47, 32, 9, QColor(p.raised))
    painter.end()
    return QIcon(pix)


class ActivityList(QWidget):
    """Rows of (dot, title + note, time). Rebuilt only when the content changes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._rows = []
        self._times: list[QLabel] = []
        self._empty = QLabel(self)
        self._empty.setObjectName("detail")
        self._empty.setWordWrap(True)
        self._empty.setContentsMargins(2, 10, 2, 10)
        self._layout.addWidget(self._empty)

    def _add_row(self):
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(2, 0, 2, 0)
        line.setSpacing(12)
        marker = EventMarker("idle", first=False, last=False, parent=row)
        line.addWidget(marker)
        text = QVBoxLayout()
        text.setContentsMargins(0, 9, 0, 9)
        text.setSpacing(1)
        title = _label("", "rowTitle", row)
        meta = _label("", "rowMeta", row, True)
        for label in (title, meta):
            label.setTextFormat(Qt.TextFormat.PlainText)
            text.addWidget(label)
        line.addLayout(text, 1)
        when = _label("", "rowMeta", row)
        when.setContentsMargins(0, 9, 0, 0)
        line.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
        self._layout.addWidget(row)
        self._rows.append((row, marker, title, meta, when))

    def set_items(self, items: list, empty_text: str) -> None:
        self._empty.setVisible(not items)
        if not items:
            self._empty.setText(empty_text)
        while len(self._rows) < len(items):
            self._add_row()
        self._times = []
        for i, (row, marker, title, meta, when) in enumerate(self._rows):
            row.setVisible(i < len(items))
            if i >= len(items):
                continue
            item = items[i]
            marker.set_segment(item.tone, i == 0, i+1 == len(items))
            title.setText(item.title)
            meta.setText(item.detail)
            meta.setVisible(bool(item.detail))
            when.setText(activity.ago(item.ts))
            self._times.append(when)


def _card(parent: QWidget, *, radius: float = 22.0, margins=(22, 18, 22, 18), spacing: int = 10):
    panel = Surface(parent, radius=min(radius, 8))
    box = QVBoxLayout(panel)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    return panel, box


def _label(text: str, name: str, parent: QWidget, wrap: bool = False) -> QLabel:
    lab = QLabel(text, parent)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class ControlCenter(QMainWindow):
    """Lifecycle UI only; it never receives biometric or liveness data."""

    def __init__(self, supervisor: SupervisorProtocol, settings_path: Path, *, async_actions: bool = False) -> None:
        super().__init__()
        self._supervisor = supervisor
        self._settings_path = Path(settings_path)
        self._appearance = QSettings(str(self._settings_path.with_name("appearance.ini")), QSettings.Format.IniFormat)
        theme = self._appearance.value("theme", "daylight")
        self.setProperty("ccReduceMotion", self._appearance.value("reduceMotion", False, type=bool))
        self.setProperty("ccTheme", theme if isinstance(theme, str) and theme in THEMES else "daylight")
        self._tray_color = None
        self._quitting = False
        self._about_dialog: QDialog | None = None
        self._touchpad_settings_button: QPushButton | None = None
        self._touchpad_note: QLabel | None = None
        self._action_note = ""
        self._async_actions = async_actions
        self._operation = None
        self._operation_poll = QTimer(self, interval=30)
        self._operation_poll.timeout.connect(self._complete_operation)
        self._resource_sampler = ResourceSampler()
        self._integrity_key: tuple | None = None
        self._recent_key = None
        self._recent_events = []
        self._history_pending = None
        self._history_poll = QTimer(self, interval=60)
        self._history_poll.timeout.connect(lambda: self._refresh_history(self._state_dir() / "audit.log"))
        self._integrity: tuple[bool, int, str] = (True, 0, "")
        self._build_window()
        self._build_tray()
        self._setup_shortcuts()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    # ------------------------------------------------------------------ build
    def _state_dir(self) -> Path:
        return self._settings_path.parent

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.start_reenrollment)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=lambda: self.show_page(len(_PAGES) - 1))
        for index in range(len(_PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self, activated=lambda i=index: self.show_page(i))

    def _build_window(self) -> None:
        self.setWindowTitle("MajestyGuard")
        self.setWindowIcon(make_tray_icon("#9DB9C4"))
        self.setMinimumSize(720, 510)
        self.resize(960, 680)
        self.setStyleSheet(stylesheet(THEMES[self.property("ccTheme")]))
        root = WorkspaceBackground(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame(root)
        header.setObjectName("masthead")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(25, 12, 25, 12)
        self._brand = BrandTrace(header, size=35)
        bar.addWidget(self._brand)
        bar.addWidget(_label("MajestyGuard", "mastBrand", header))
        bar.addSpacing(5)
        bar.addWidget(_label("Control Center", "mastSub", header))
        bar.addStretch()
        self._compact_status = Dot(parent=header)
        bar.addWidget(self._compact_status)
        self._side_pill = _label("", "mastSub", header)
        bar.addWidget(self._side_pill)
        bar.addSpacing(12)
        self._quick_button = QPushButton("Quick controls", header)
        self._quick_button.setObjectName("mastButton")
        self._quick_button.setToolTip("Open the notification-area controls")
        self._quick_button.clicked.connect(self._quick_from_window)
        bar.addWidget(self._quick_button)
        self._info_button = QPushButton("i", header)
        self._info_button.setObjectName("mastInfo")
        self._info_button.setFixedSize(32, 32)
        self._info_button.setAccessibleName("About MajestyGuard")
        self._info_button.setToolTip("About MajestyGuard")
        self._info_button.clicked.connect(self.show_about)
        bar.addWidget(self._info_button)
        outer.addWidget(header)
        self._tab_bar = TabStrip(root)
        tabs = QHBoxLayout(self._tab_bar)
        tabs.setContentsMargins(20, 0, 20, 0)
        tabs.setSpacing(0)
        self._nav = []
        for i, (kind, title, _) in enumerate(_PAGES):
            btn = NavButton(kind, title, self._tab_bar)
            btn.setToolTip(f"{title}  (Ctrl+{i + 1})")
            btn.clicked.connect(lambda _, index=i: self.show_page(index))
            tabs.addWidget(btn, 1)
            self._nav.append(btn)
        outer.addWidget(self._tab_bar)
        rule = QFrame(root)
        rule.setObjectName("hair")
        outer.addWidget(rule)
        content = QVBoxLayout()
        content.setContentsMargins(24, 18, 24, 16)
        content.setSpacing(6)
        self._page_title = _label("", "pageTitle", root)
        self._page_sub = _label("", "pageSub", root, True)
        content.addWidget(self._page_title)
        content.addWidget(self._page_sub)
        self._stack = QStackedWidget(root)
        content.addWidget(self._stack, 1)
        outer.addLayout(content, 1)

        for builder in (self._page_overview, self._page_protection,
                        self._page_activity, self._page_enrollment, self._page_appearance,
                        self._page_diagnostics):
            scroll = QScrollArea(self._stack)
            scroll.setObjectName("pageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(builder())
            self._stack.addWidget(scroll)
        self.show_page(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_nav"):
            for nav in self._nav:
                nav.compact = self.width() < 820
                nav.update()
        if hasattr(self, "_overview_split"):
            self._overview_split.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 820
                                              else QBoxLayout.Direction.LeftToRight)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        page.setObjectName("pageContent")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)
        return page, box

    def _page_overview(self) -> QWidget:
        page, box = self._page()
        split = QHBoxLayout()
        self._overview_split = split
        split.setSpacing(24)
        plate = QFrame(page)
        plate.setObjectName("aperturePlate")
        deck = QVBoxLayout(plate)
        deck.setContentsMargins(20, 16, 20, 20)
        deck.setSpacing(7)
        title = QHBoxLayout()
        title.addWidget(_label("CAMERA", "engraving", plate))
        title.addStretch()
        self._camera_caption = _label("", "engraving", plate)
        title.addWidget(self._camera_caption)
        deck.addLayout(title)
        self._aperture = Aperture(plate)
        deck.addWidget(self._aperture, 1)
        self._status_label = _label("", "plateTitle", plate, True)
        self._detail_label = _label("", "plateDetail", plate, True)
        self._detail_label.setTextFormat(Qt.TextFormat.PlainText)
        deck.addWidget(self._status_label)
        deck.addWidget(self._detail_label)
        deck.addSpacing(5)
        self._primary_button = TransportButton(plate)
        self._primary_button.setObjectName("plateAction")
        self._primary_button.clicked.connect(self._run_primary_action)
        deck.addWidget(self._primary_button, 0, Qt.AlignmentFlag.AlignLeft)
        split.addWidget(plate, 4)
        journal = QWidget(page)
        body = QVBoxLayout(journal)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(4)
        body.addWidget(_label("This session", "journalTitle", journal))
        values = []
        for caption, index in (("Idle check", 1), ("Camera", _ACTIVITY_PAGE), ("Last unlock", _ACTIVITY_PAGE)):
            button = QPushButton(journal)
            button.setObjectName("ledgerRow")
            button.setMinimumHeight(40)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(caption)
            button.setToolTip("Open " + _PAGES[index][1])
            button.clicked.connect(lambda _, i=index: self.show_page(i))
            row = QHBoxLayout(button)
            row.setContentsMargins(5, 2, 5, 2)
            row.addWidget(_label(caption, "detail", button), 1)
            value = _label("--", "ledgerValue", button)
            row.addWidget(value)
            row.addSpacing(6)
            row.addWidget(_label("\u203a", "quickArrow", button))
            for label in button.findChildren(QLabel):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            body.addWidget(button)
            values.append(value)
        self._stat_timeout, self._stat_camera, self._stat_last = values
        body.addSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_label("Recent activity", "cardTitle", journal), 1)
        more = QPushButton("View all", journal)
        more.setObjectName("link")
        more.clicked.connect(lambda: self.show_page(_ACTIVITY_PAGE))
        heading.addWidget(more)
        body.addLayout(heading)
        self._recent_list = ActivityList(journal)
        body.addWidget(self._recent_list)
        body.addStretch()
        split.addWidget(journal, 5)
        box.addLayout(split, 1)
        rule = QFrame(page)
        rule.setObjectName("hair")
        box.addWidget(rule)
        footer = QHBoxLayout()
        values = []
        for caption in ("Face profile", "Session state", "Last cold start"):
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(_label(caption, "rowMeta", page))
            value = _label("", "rowTitle", page, True)
            column.addWidget(value)
            values.append(value)
            footer.addLayout(column, 1)
        self._gallery_chip, self._lock_chip, self._perf_chip = values
        box.addLayout(footer)
        return page

    def _stat_card(self, parent: QWidget, row: QHBoxLayout, caption: str, page_index: int | None = None) -> QLabel:
        card, box = _card(parent, radius=20, margins=(20, 14, 20, 14), spacing=2)
        heading = QHBoxLayout()
        heading.addWidget(_label(caption, "statLabel", card), 1)
        if page_index is not None:
            go = QPushButton("\u203a", card)
            go.setObjectName("statLink")
            go.setFixedSize(24, 24)
            destination = "Open " + _PAGES[page_index][1]
            go.setAccessibleName(destination)
            go.setToolTip(destination)
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.clicked.connect(lambda _checked=False: self.show_page(page_index))
            heading.addWidget(go)
        box.addLayout(heading)
        value = _label("--", "value", card, True)
        box.addWidget(value)
        row.addWidget(card, 1)
        return value

    def _page_protection(self) -> QWidget:
        page, box = self._page()
        idle, idle_box = _card(page)
        idle_box.addWidget(_label("Lock after inactivity", "cardTitle", idle))
        idle_box.addWidget(_label("After this interval, MajestyGuard checks for your face before showing the privacy overlay.", "detail", idle, True))
        row = QHBoxLayout()
        row.setSpacing(10)
        self._timeout_input = QSpinBox(idle)
        self._timeout_input.setRange(15, 1800)
        self._timeout_input.setSuffix(" seconds")
        self._timeout_input.setMinimumWidth(150)
        self._saved_timeout = self._current_idle_timeout()
        self._timeout_input.setValue(self._saved_timeout)
        self._timeout_input.valueChanged.connect(lambda _v: self._mark_current_preset())
        row.addWidget(self._timeout_input)
        self._save_timeout_button = QPushButton("Save", idle)
        self._save_timeout_button.setObjectName("secondary")
        self._save_timeout_button.clicked.connect(self.save_idle_timeout)
        row.addWidget(self._save_timeout_button)
        row.addStretch(1)
        idle_box.addLayout(row)
        row = QHBoxLayout()
        self._preset_buttons: list[QPushButton] = []
        for label_text, secs in (("30s", 30), ("1m", 60), ("2m", 120), ("5m", 300)):
            btn = QPushButton(label_text, idle)
            btn.setObjectName("secondary")
            btn.setFixedHeight(36)
            btn.setProperty("seconds", secs)
            btn.clicked.connect(lambda _c=False, s=secs: self.apply_timeout_preset(s))
            self._preset_buttons.append(btn)
            row.addWidget(btn)
        row.addStretch(1)
        idle_box.addLayout(row)
        self._timeout_note = _label("Changes apply on the next start.", "detail", idle, True)
        idle_box.addWidget(self._timeout_note)
        box.addWidget(idle)

        box.addStretch(1)
        self._mark_current_preset()
        return page

    def _page_diagnostics(self) -> QWidget:
        page, box = self._page()
        row = QHBoxLayout()
        row.setSpacing(14)
        self._worker_ram = self._stat_card(page, row, "WORKER RAM")
        self._center_ram = self._stat_card(page, row, "APP RAM")
        self._worker_cpu = self._stat_card(page, row, "WORKER CPU")
        self._worker_ram.setToolTip("Combined working set of the protection worker and its helpers.")
        self._center_ram.setToolTip("Working set of the Control Center, which stays open when paused.")
        self._worker_cpu.setToolTip("Share of total CPU capacity used by the protection worker and helpers.")
        box.addLayout(row)
        trace_card, trace_box = _card(page, margins=(18, 12, 18, 10), spacing=3)
        trace_box.addWidget(_label("Worker memory", "rowTitle", trace_card))
        self._memory_trace = MemoryTrace(trace_card)
        trace_box.addWidget(self._memory_trace)
        box.addWidget(trace_card)
        card, detail = _card(page)
        header = QHBoxLayout()
        header.addWidget(_label("Processes", "cardTitle", card), 1)
        logs = QPushButton("Open logs", card)
        logs.setObjectName("secondary")
        logs.clicked.connect(self.open_diagnostics)
        self._stop_button = QPushButton("Stop", card)
        self._stop_button.setAccessibleName("Stop protection")
        self._stop_button.setToolTip("Stop protection and release its worker memory.")
        self._stop_button.setObjectName("secondary")
        self._stop_button.clicked.connect(self.stop_protection)
        header.addWidget(self._stop_button)
        header.addWidget(logs)
        detail.addLayout(header)
        self._process_detail = _label("", "mono", card, True)
        self._process_detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.addWidget(self._process_detail)
        self._diagnostic_note = _label("", "detail", card, True)
        self._diagnostic_note.setTextFormat(Qt.TextFormat.PlainText)
        self._diagnostic_note.hide()
        detail.addWidget(self._diagnostic_note)
        copy_row = QHBoxLayout()
        self._copy_diagnostics_button = QPushButton("Copy summary", card)
        self._copy_diagnostics_button.setObjectName("secondary")
        self._copy_diagnostics_button.setToolTip("Copy status and metrics, without logs or personal data.")
        self._copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        copy_row.addWidget(self._copy_diagnostics_button)
        copy_row.addStretch(1)
        detail.addLayout(copy_row)
        box.addWidget(card)
        timings = QHBoxLayout()
        timings.setSpacing(14)
        self._verify_time = self._stat_card(page, timings, "LAST FACE UNLOCK")
        self._camera_open_time = self._stat_card(page, timings, "CAMERA START")
        box.addLayout(timings)
        box.addStretch(1)
        return page

    def _refresh_diagnostics(self) -> None:
        if self._operation is not None or self.current_page() != len(_PAGES) - 1:
            return
        status = self._supervisor.status()
        self._stop_button.setEnabled(
            status in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING, RuntimeStatus.DEGRADED)
            and not self._controls_locked()
        )
        verify_time, camera_time = read_verify_timing(self._state_dir() / "daemon.log")
        if verify_time != "--" or not self._verify_time.property("hasReading"):
            self._verify_time.setText(verify_time)
            self._camera_open_time.setText(camera_time)
            self._verify_time.setProperty("hasReading", verify_time != "--")
        query = getattr(self._supervisor, "diagnostic_processes", None)
        try:
            readings = self._resource_sampler.sample(query()) if callable(query) else None
        except (OSError, RuntimeError):
            readings = None
        if readings is None:
            self._memory_trace.observe(None)
            for label in (self._worker_ram, self._center_ram, self._worker_cpu):
                label.setText("--")
            self._process_detail.setText("Process readings unavailable.")
            return

        def ram(amount: int) -> str:
            return f"{amount / (1024 ** 2):.1f} MiB" if amount else "0 MiB"

        workers = [item for item in readings if item.group == "Protection"]
        centers = [item for item in readings if item.group == "Control Center"]
        self._memory_trace.observe(sum(item.ram_bytes for item in workers)
                                   if all(item.ram_bytes is not None for item in workers) else None)
        for label, group in ((self._worker_ram, workers), (self._center_ram, centers)):
            label.setText(ram(sum(item.ram_bytes for item in group))
                          if all(item.ram_bytes is not None for item in group) else "--")
        self._worker_cpu.setText(f"{sum(item.cpu_percent for item in workers):.1f}%"
                                 if all(item.cpu_percent is not None for item in workers) else "--")
        lines = []
        for item in readings:
            memory = ram(item.ram_bytes) if item.ram_bytes is not None else "--"
            cpu = f"{item.cpu_percent:.1f}% CPU" if item.cpu_percent is not None else "CPU --"
            uptime = self._format_seconds(int(item.uptime_seconds)) if item.uptime_seconds is not None else "--"
            lines.append(f"{item.group}  |  PID {item.pid}  |  {memory}  |  {cpu}  |  Up {uptime}")
        self._process_detail.setText("\n".join(lines) or "No processes to show.")

    def _page_activity(self) -> QWidget:
        page, box = self._page()
        card, cbox = _card(page, radius=28, margins=(26, 22, 26, 22))
        row = QHBoxLayout()
        row.setSpacing(16)
        self._camera_dot = Dot("idle", 16, card)
        row.addWidget(self._camera_dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._camera_headline = _label("No camera data yet", "status", card)
        text.addWidget(self._camera_headline)
        self._camera_hint = _label("Camera status appears here while protection is running.", "detail", card, True)
        text.addWidget(self._camera_hint)
        row.addLayout(text, 1)
        cbox.addLayout(row)
        box.addWidget(card)

        self._camera_meta = _label("", "rowMeta", card, True)
        cbox.addWidget(self._camera_meta)
        self._camera_events = _label("", "mono", card, True)
        self._camera_events.setTextFormat(Qt.TextFormat.PlainText)
        cbox.addWidget(self._camera_events)
        banner, bbox = _card(page, radius=20, margins=(20, 14, 20, 14))
        row = QHBoxLayout()
        row.setSpacing(12)
        self._integrity_dot = Dot("ok", 12, banner)
        row.addWidget(self._integrity_dot)
        col = QVBoxLayout()
        col.setSpacing(1)
        self._integrity_title = _label("Recent events", "rowTitle", banner)
        self._integrity_meta = _label("", "rowMeta", banner, True)
        col.addWidget(self._integrity_title)
        col.addWidget(self._integrity_meta)
        row.addLayout(col, 1)
        folder = QPushButton("Open log folder", banner)
        folder.setObjectName("secondary")
        folder.clicked.connect(self.open_diagnostics)
        row.addWidget(folder)
        bbox.addLayout(row)
        box.addWidget(banner)
        card, cbox = _card(page, margins=(22, 14, 22, 10), spacing=2)
        filters = QHBoxLayout()
        self._activity_search = QLineEdit(card)
        self._activity_search.setObjectName("activitySearch")
        self._activity_search.setPlaceholderText("Search recent events")
        self._activity_search.setAccessibleName("Search the latest 40 events")
        self._activity_search.setClearButtonEnabled(True)
        self._alerts_only = QPushButton("Warnings only", card)
        self._alerts_only.setObjectName("filter")
        self._alerts_only.setCheckable(True)
        self._activity_search.textChanged.connect(self._filter_activity)
        self._alerts_only.toggled.connect(self._filter_activity)
        filters.addWidget(self._activity_search, 1)
        filters.addWidget(self._alerts_only)
        cbox.addLayout(filters)
        self._activity_list = ActivityList(card)
        cbox.addWidget(self._activity_list)
        cbox.addStretch(1)
        box.addWidget(card, 1)
        return page

    def _filter_activity(self, *_):
        query = self._activity_search.text().casefold().strip()
        alerts = self._alerts_only.isChecked()
        items = [item for item in getattr(self, "_recent_events", ())
                 if (not alerts or item.tone in {"bad", "warn"})
                 and query in (item.title + " " + item.detail).casefold()]
        self._activity_list.set_items(items, "No matching events in the latest 40." if query or alerts
                                      else "No activity recorded yet.")

    def _page_enrollment(self) -> QWidget:
        page, box = self._page()
        card, cbox = _card(page, radius=28, margins=(26, 22, 26, 22))
        row = QHBoxLayout()
        row.setSpacing(16)
        self._enroll_dot = Dot("ok", 16, card)
        row.addWidget(self._enroll_dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._enroll_title = _label("Face profile ready", "status", card, True)
        self._enroll_sub = _label("", "detail", card, True)
        text.addWidget(self._enroll_title)
        text.addWidget(self._enroll_sub)
        row.addLayout(text, 1)
        self._reenroll_btn = QPushButton("Re-enroll face", card)
        self._reenroll_btn.setMinimumWidth(170)
        self._reenroll_btn.clicked.connect(self.start_reenrollment)
        row.addWidget(self._reenroll_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cbox.addLayout(row)
        box.addWidget(card)
        self._enroll_result = _label("", "detail", page, True)
        self._enroll_result.hide()
        box.addWidget(self._enroll_result)
        reset, reset_box = _card(page)
        reset_box.addWidget(_label("Replace face profile", "cardTitle", reset))
        reset_box.addWidget(_label("Use this if your existing profile no longer matches. "
                                   "Windows confirms your account before the new scan starts.", "detail", reset, True))
        self._startover_btn = QPushButton("Start over...", reset)
        self._startover_btn.setObjectName("secondary")
        self._startover_btn.clicked.connect(self.start_over_enrollment)
        reset_box.addWidget(self._startover_btn, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(reset)
        tips, tbox = _card(page)
        tbox.addWidget(_label("For a profile that works everywhere", "cardTitle", tips, True))
        for tip in (
            "Sit where you normally sit, at your usual distance from the screen.",
            "Use the light you usually work in, then add a second pass in dimmer or brighter light.",
            "Follow the ring: turn slowly so every direction fills up before you finish.",
            "Wear what you normally wear. Glasses on if you use them day to day.",
            "Protection pauses while you enroll and resumes by itself afterwards.",
        ):
            line = QHBoxLayout()
            line.setSpacing(10)
            line.addWidget(Dot("info", 8, tips), 0, Qt.AlignmentFlag.AlignTop)
            line.addWidget(_label(tip, "detail", tips, True), 1)
            tbox.addLayout(line)
        box.addWidget(tips)
        box.addStretch(1)
        return page

    def show_about(self) -> None:
        if self._operation is not None or self._controls_locked():
            return
        if self._about_dialog is not None:
            self._about_dialog.raise_()
            return
        page = QDialog(self)
        page.setWindowTitle("About MajestyGuard")
        page.setObjectName("aboutDialog")
        page.setProperty("ccTheme", self.property("ccTheme"))
        page.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        page.finished.connect(self._about_closed)
        page.setMinimumWidth(350)
        screen = self.screen()
        height = min(650, screen.availableGeometry().height() - 84) if screen else 570
        page.resize(490, max(300, height))
        box = QVBoxLayout(page)
        header = QHBoxLayout()
        mark = QLabel(page)
        mark.setPixmap(make_tray_icon("#9DB9C4").pixmap(43, 43))
        header.addWidget(mark)
        header.addWidget(_label("MajestyGuard", "pageTitle", page), 1)
        box.addLayout(header)
        scroll = QScrollArea(page)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        body.setObjectName("pageContent")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        scroll.setWidget(body)
        box.addWidget(scroll, 1)
        priv, pbox = _card(body)
        pbox.addWidget(_label("Privacy overlay", "cardTitle", priv))
        pbox.addWidget(_label("The overlay covers your desktop while apps keep running. It is not the Windows lock screen. "
                              "Windows gestures and system windows can still appear above it.", "detail", priv, True))
        controls = QGridLayout()
        controls.setHorizontalSpacing(12)
        controls.setVerticalSpacing(8)
        for row, (key, title, detail) in enumerate((
            ("Space", "Verify your face", "Look at the camera to resume. Repeated failures increase the retry delay."),
            ("Tab twice", "Windows sign-in fallback", "Switch to Windows lock, then sign in as usual."),
            ("Win + L", "Lock from the desktop", "Use Windows lock before leaving the PC unattended."),
        )):
            cap = _label(key, "keycap", priv)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            controls.addWidget(cap, row, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(_label(title, "rowTitle", priv))
            text.addWidget(_label(detail, "rowMeta", priv, True))
            controls.addLayout(text, row, 1)
        controls.setColumnStretch(1, 1)
        pbox.addLayout(controls)
        self._touchpad_settings_button = QPushButton("Touchpad settings", priv)
        self._touchpad_settings_button.setObjectName("secondary")
        self._touchpad_settings_button.clicked.connect(self.open_touchpad_settings)
        pbox.addWidget(self._touchpad_settings_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._touchpad_note = _label("", "detail", priv, True)
        self._touchpad_note.hide()
        pbox.addWidget(self._touchpad_note)
        content.addWidget(priv)
        files, fbox = _card(body)
        fbox.addWidget(_label("Local data", "cardTitle", files))
        fbox.addWidget(_label("Your encrypted face profile stays on this PC. Camera frames are not saved or uploaded.",
                              "detail", files, True))
        directory = QLineEdit(str(self._state_dir()), files)
        directory.setReadOnly(True)
        directory.setObjectName("folderPath")
        directory.setAccessibleName("Local data folder")
        directory.setToolTip(directory.text())
        directory.setCursorPosition(0)
        fbox.addWidget(directory)
        content.addWidget(files)
        keys, kbox = _card(body)
        kbox.addWidget(_label("Keyboard shortcuts", "cardTitle", keys))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for r, (combo, what) in enumerate(((f"Ctrl + 1 to {len(_PAGES)}", "Switch tab"), ("Ctrl + R", "Re-enroll face"),
                                           ("Ctrl + D", "Open diagnostics"))):
            cap = _label(combo, "keycap", keys)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(cap, r, 0)
            grid.addWidget(_label(what, "detail", keys), r, 1)
        grid.setColumnStretch(2, 1)
        kbox.addLayout(grid)
        content.addWidget(keys)
        content.addStretch()
        close = QPushButton("Done", page)
        close.clicked.connect(page.accept)
        box.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._about_dialog = page
        page.open()

    def _about_closed(self, _result: int) -> None:
        self._about_dialog = None
        self._touchpad_settings_button = None
        self._touchpad_note = None

    def _page_appearance(self) -> QWidget:
        page, box = self._page()
        choices = QGridLayout()
        choices.setSpacing(10)
        self._theme_group = QButtonGroup(page)
        for i, (name, palette) in enumerate(THEMES.items()):
            button = QPushButton(name.title(), page)
            button.setObjectName("themeOption")
            button.setIcon(_palette_icon(palette))
            button.setIconSize(QSize(84, 48))
            button.setCheckable(True)
            button.setChecked(name == self.property("ccTheme"))
            button.setMinimumHeight(78)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._theme_group.addButton(button)
            button.clicked.connect(lambda _checked, key=name: self._select_theme(key))
            choices.addWidget(button, i // 2, i % 2)
        box.addLayout(choices)
        self._theme_note = _label("Applies to the Control Center only.", "detail", page, True)
        box.addWidget(self._theme_note)
        motion, motion_box = _card(page)
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.addWidget(_label("Reduce motion", "rowTitle", motion))
        copy.addWidget(_label("Skip page and tray animations. Protection is unchanged.", "detail", motion, True))
        row.addLayout(copy, 1)
        self._motion_switch = Toggle(motion)
        self._motion_switch.setAccessibleName("Reduce Control Center motion")
        self._motion_switch.setChecked(reduced_motion(self))
        self._motion_switch.setDisabled(os.environ.get("MG_UI_REDUCE_MOTION") == "1")
        self._motion_switch.toggled.connect(self._set_reduced_motion)
        row.addWidget(self._motion_switch)
        motion_box.addLayout(row)
        box.addWidget(motion)
        box.addStretch(1)
        return page

    def _set_reduced_motion(self, value):
        self.setProperty("ccReduceMotion", value)
        self._quick.setProperty("ccReduceMotion", value)
        self._brand.set_busy(self._operation is not None)
        self._quick.brand.set_busy(self._operation is not None)
        if value:
            self._tab_bar.select(self._nav[self.current_page()], False)
        self._aperture.sync_motion_preference()
        self._quick.aperture.sync_motion_preference()
        self._appearance.setValue("reduceMotion", value)
        self._appearance.sync()
        if self._appearance.status() != QSettings.Status.NoError:
            self._theme_note.setText("Applied for this session. Could not save the motion preference.")

    def _select_theme(self, key: str) -> None:
        if key == self.property("ccTheme") or key not in THEMES:
            return
        self.setProperty("ccTheme", key)
        self._quick.setProperty("ccTheme", key)
        self.setStyleSheet(stylesheet(THEMES[key]))
        for child in self.findChildren(QWidget):
            child.update()
        self._appearance.setValue("theme", key)
        self._appearance.sync()
        if self._appearance.status() != QSettings.Status.NoError:
            self._theme_note.setText("Applied for this session. Could not save the theme.")

    def _build_tray(self) -> None:
        self._quick = QuickControls(self)
        self._quick.setProperty("ccTheme", self.property("ccTheme"))
        self._quick.setProperty("ccReduceMotion", self.property("ccReduceMotion"))
        self._quick.primaryRequested.connect(self._run_primary_action)
        self._quick.timeoutRequested.connect(self._quick_timeout)
        self._quick.destinationRequested.connect(self._open_destination)
        self._tray = QSystemTrayIcon(make_tray_icon("#7f8b9c"), self)
        self._tray.setToolTip("MajestyGuard")
        menu = QMenu(self)
        self._tray_primary_action = QAction(self)
        self._tray_primary_action.triggered.connect(self._run_primary_action)
        menu.addAction(self._tray_primary_action)
        for text, slot in (("Open Control Center", self.show_center),
                           ("Face profile", lambda: self._open_destination("enrollment")),
                           ("Diagnostics", lambda: self._open_destination("diagnostics"))):
            action = QAction(text, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit MajestyGuard", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._handle_tray_activation)

    # --------------------------------------------------------------- queries
    def show_page(self, index: int) -> None:
        index = max(0, min(len(_PAGES) - 1, int(index)))
        self._stack.setCurrentIndex(index)
        self._nav[index].setChecked(True)
        self._tab_bar.select(self._nav[index])
        _kind, title, sub = _PAGES[index]
        self._page_title.setText(title)
        self._page_sub.setText(sub)
        self._page_title.setVisible(index != 0)
        self._page_sub.setVisible(index != 0)
        if self._operation is None:
            self._refresh_pages()

    def _open_destination(self, kind):
        self._quick.hide()
        if self._operation is not None or self._controls_locked():
            return
        index = next(i for i, (key, _, _) in enumerate(_PAGES) if key == kind)
        self.show_page(index)
        self.show_center()

    def _quick_from_window(self):
        button = self._quick_button
        self._present_quick(QRect(button.mapToGlobal(QPoint()), button.size()))

    def _quick_timeout(self, seconds):
        self.apply_timeout_preset(seconds)
        self._action_note = self._timeout_note.text()
        self.refresh()

    def _present_quick(self, anchor):
        if self._quick.isVisible():
            self._quick.hide()
            return
        if self._operation is not None:
            return
        self.refresh()
        if self._controls_locked():
            return
        screen = QApplication.screenAt(anchor.center()) or self.screen()
        if screen is not None:
            self._quick.present(anchor, screen.availableGeometry())

    def current_page(self) -> int:
        return self._stack.currentIndex()

    def status_text(self) -> str:
        return self._status_label.text()

    def primary_action_text(self) -> str:
        return self._primary_button.text()

    def enrollment_present(self) -> bool:
        return self._supervisor.enrollment_present()

    def timeout_note_text(self) -> str:
        return self._timeout_note.text()

    def _controls_locked(self) -> bool:
        check = getattr(self._supervisor, "controls_locked", None)
        try:
            return bool(check()) if callable(check) else False
        except Exception:
            return True

    def _current_idle_timeout(self) -> int:
        try:
            from app.config import load_settings

            return load_settings(self._settings_path).idle_timeout_seconds
        except OSError:
            return 60

    def _mark_current_preset(self) -> None:
        """Highlight the preset that matches the timeout currently in the box."""
        value = self._timeout_input.value()
        for btn in self._preset_buttons:
            btn.setProperty("current", "true" if btn.property("seconds") == value else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # --------------------------------------------------------------- refresh
    def _apply_lock_state(self, locked: bool) -> None:
        for widget in (self._reenroll_btn, self._startover_btn, self._timeout_input,
                       self._save_timeout_button, self._info_button, *self._preset_buttons):
            widget.setEnabled(not locked)
        if locked:
            if self._about_dialog is not None:
                self._about_dialog.reject()
            self._primary_button.setEnabled(False)
            self._tray_primary_action.setEnabled(False)
            self._detail_label.setText(_LOCKED_NOTE)

    def _refuse_while_locked(self) -> bool:
        if self._operation is not None:
            return True
        if not self._controls_locked():
            return False
        self._timeout_note.setText(_LOCKED_NOTE)
        self._diagnostic_note.setText(_LOCKED_NOTE)
        self._diagnostic_note.show()
        return True

    def _refresh_telemetry_chips(self, enrolled: bool) -> str:
        state_dir = self._state_dir()
        gallery = activity.gallery_summary(state_dir / "embeddings_v2_meta.json") if enrolled else "Not enrolled"
        self._gallery_chip.setText(gallery)

        lock_file = state_dir / "lock_state.txt"
        try:
            lock_state = lock_file.read_text(encoding="utf-8").strip()
        except OSError:
            lock_state = ""
        self._lock_chip.setText(lock_state.replace("_", " ").capitalize() or "Not reported")

        perf_text = "Not measured"
        log_file = state_dir / "daemon.log"
        if log_file.is_file():
            try:
                # Only the last 4 KB matters; never slurp a multi-MB log every 2 s.
                with log_file.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    handle.seek(max(0, handle.tell() - 4096))
                    tail = handle.read().decode("utf-8", errors="replace")
                for line in reversed(tail.splitlines()):
                    if "total=" in line and "Cold start complete:" in line:
                        fields = line.split("total=", 1)[1].split()
                        if fields and fields[0].endswith("s"):
                            token = fields[0]
                            try:
                                seconds = (int(token[:-2]) / 1000 if token.endswith("ms")
                                           else float(token[:-1]))
                            except ValueError:
                                continue
                            if 0 <= seconds < float("inf"):
                                perf_text = f"{seconds:.2f}s"
                                break
            except OSError:
                pass
        self._perf_chip.setText(perf_text)
        return gallery

    def refresh(self) -> None:
        if self._operation is not None:
            return
        maintain = getattr(self._supervisor, "maintain", None)
        if callable(maintain):
            maintain()
        enrolled = self._supervisor.enrollment_present()
        status = self._supervisor.status()
        if not enrolled:
            text, action, color = "Enrollment required", "Enroll face", "#c06a00"
            detail = "Create a local face profile before starting protection."
            pill, tone = "Enroll to start", "warn"
        else:
            text, action, color = _STATUS_COPY[status]
            detail = {
                RuntimeStatus.STOPPED: "Presence checks are off.",
                RuntimeStatus.STARTING: "Preparing the recognition engine.",
                RuntimeStatus.RUNNING: "Monitoring this Windows session.",
                RuntimeStatus.PAUSED: "Presence checks are paused until you resume.",
                RuntimeStatus.DEGRADED: "Protection stopped unexpectedly. Restart to resume checks.",
            }[status]
            note = getattr(self._supervisor, "recovery_note", None)
            if callable(note):
                detail = note() or detail
            pill, tone = _SIDE_PILL[status]
        enrolling = getattr(self._supervisor, "enrollment_running", lambda: False)()
        if enrolling:
            detail = "Face setup is open. Protection cannot use the camera until it closes."
            if status is not RuntimeStatus.RUNNING:
                action = "Face setup open"
        elif self._action_note:
            detail = self._action_note
        self._status_label.setText(text)
        self._detail_label.setText(detail)
        self._diagnostic_note.setText(self._action_note)
        self._diagnostic_note.setVisible(bool(self._action_note))
        self._primary_button.setText(action)
        self._primary_button.setEnabled(status is not RuntimeStatus.STARTING and not enrolling)
        self._tray_primary_action.setText(action)
        self._tray_primary_action.setEnabled(status is not RuntimeStatus.STARTING and not enrolling)
        self._side_pill.setText(pill)
        self._compact_status.set_tone(tone)
        self._compact_status.setAccessibleName(pill)
        self._compact_status.setToolTip(pill)
        if color != self._tray_color:
            self.setProperty("statusInk", color)
            self._tray.setIcon(make_tray_icon(color))
            self._tray_color = color
        self._enrolled = enrolled
        artwork = ("starting" if status is RuntimeStatus.STARTING else
                   "running" if status is RuntimeStatus.RUNNING else None)
        self._aperture.set_protection_art(artwork if enrolled and not enrolling else None)
        # Keep supervision and command guards alive in the tray. The hidden window
        # need not scan log tails, rebuild timelines or poll process memory.
        if self.sender() is not self._refresh_timer or self.isVisible() or self._quick.isVisible():
            self._gallery_text = self._refresh_telemetry_chips(enrolled)
            self._refresh_pages()
        locked = self._controls_locked()
        self._apply_lock_state(locked)
        self._quick_button.setEnabled(not locked)
        if locked:
            self._quick.hide()
        self._quick.headline.setText(text)
        self._quick.note.setText(self._detail_label.text())
        self._quick.primary.setText(action)
        self._quick.primary.setEnabled(self._primary_button.isEnabled())
        for seconds, button in self._quick.presets.items():
            button.setChecked(self._saved_timeout == seconds)
            button.setEnabled(not locked)
        for key, value in (("activity", self._stat_camera.text()), ("protection", self._stat_timeout.text())):
            self._quick.readings[key].setText(value)
            self._quick.routes[key].setAccessibleDescription(value)
        self._tray.setToolTip("MajestyGuard: " + ("Session locked" if locked else text))
        if self._quick.isVisible():
            self._quick.adjustSize()
        if enrolling:
            self._reenroll_btn.setEnabled(False)
            self._startover_btn.setEnabled(False)

    def _refresh_pages(self) -> None:
        """Fill the data-driven parts of every tab (cheap: tail reads, cached integrity)."""
        state = self._state_dir()
        enrolled = self._enrolled if hasattr(self, "_enrolled") else self._supervisor.enrollment_present()
        self._stat_timeout.setText(self._format_seconds(self._saved_timeout))
        self._refresh_history(state / "audit.log")

        cam = activity.read_camera_health(state / "camera_health.json")
        running = self._operation is None and self._supervisor.status() is RuntimeStatus.RUNNING
        parked = cam is not None and cam.capture_open is False and cam.status == "healthy"
        live = cam is not None and (cam.age_s < 60 or parked) and running
        if cam is None or not live:
            self._camera_dot.set_tone("idle")
            self._camera_headline.setText("No live camera data")
            self._camera_hint.setText("Camera status appears here while protection is running.")
            self._stat_camera.setText("Idle")
            self._camera_meta.setText("")
            self._camera_meta.hide()
            self._camera_events.setText("")
            self._nav[_ACTIVITY_PAGE].set_badge("")
        else:
            healthy = cam.status == "healthy"
            tone = "idle" if parked else ("ok" if healthy else ("bad" if cam.status == "failed" else "warn"))
            self._camera_dot.set_tone(tone)
            self._camera_headline.setText("Camera parked" if parked else
                                          cam.headline or ("Camera is healthy" if healthy else "Camera needs attention"))
            self._camera_hint.setText("MajestyGuard isn't using the camera." if parked else
                                      cam.hint or ("Frames are arriving normally." if healthy else "Retrying automatically."))
            self._stat_camera.setText("Parked" if parked else ("Healthy" if healthy else cam.status.capitalize()))
            camera_details = []
            if cam.backend not in ("", "-"):
                camera_details.append(f"Backend: {cam.backend}")
            if cam.recoveries:
                camera_details.append(f"Recovered: {cam.recoveries}")
            if cam.next_retry_s > 0.5 and not parked:
                camera_details.append(f"Retry in {cam.next_retry_s:.0f} s")
            self._camera_meta.setText("  |  ".join(camera_details))
            self._camera_meta.setVisible(bool(camera_details))
            self._camera_events.setText("\n".join(cam.events[-2:]) if not healthy else "")
            self._nav[_ACTIVITY_PAGE].set_badge("" if healthy else "!", tone)
        self._camera_events.setVisible(bool(self._camera_events.text()))
        reading = ("unknown" if not live else "fault" if cam.status != "healthy" else
                   "parked" if parked else "open" if cam.capture_open is True else "unknown")
        self._aperture.set_reading(reading)
        self._camera_caption.setText({"parked": "Parked", "fault": "Needs attention",
                                      "open": "In use", "unknown": "Not reported"}[reading])
        if hasattr(self, "_quick"):
            self._quick.aperture.set_reading(reading)

        gallery = getattr(self, "_gallery_text", "")
        outcome = activity.read_enroll_result(state)
        self._enroll_result.setText(outcome or "")
        self._enroll_result.setVisible(outcome is not None)
        if enrolled:
            self._enroll_dot.set_tone("ok")
            self._enroll_title.setText("Face profile ready")
            self._enroll_sub.setText(f"{gallery}. Re-enroll if your look changes a lot or recognition feels off.")
            self._reenroll_btn.setText("Re-enroll face")
            self._nav[_ENROLLMENT_PAGE].set_badge("")
        else:
            self._enroll_dot.set_tone("warn")
            self._enroll_title.setText("No face profile yet")
            self._enroll_sub.setText("Create a local face profile to switch protection on.")
            self._reenroll_btn.setText("Enroll face")
            self._nav[_ENROLLMENT_PAGE].set_badge("!", "warn")
        self._refresh_diagnostics()

    def _refresh_history(self, path: Path) -> None:
        check_chain = self.current_page() == _ACTIVITY_PAGE
        try:
            stat = path.stat()
            key = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            key = ()
            self._recent_key, self._recent_events = key, []
            self._integrity_key, self._integrity = key, (True, 0, "No activity recorded yet")
        except OSError as exc:
            key = ()
            self._recent_key, self._recent_events = key, []
            self._integrity_key, self._integrity = key, (False, 0, f"Cannot read the log ({type(exc).__name__})")
        if self._history_pending is not None:
            pending_key, future = self._history_pending
            if future.done():
                self._history_pending = None
                # A new append must not starve the timeline. Show the completed
                # snapshot, but only attest integrity for the exact file version.
                if pending_key == key or (key and pending_key and pending_key[0] == key[0]
                                          and pending_key[2] < key[2]):
                    try:
                        self._recent_events, integrity = future.result()
                    except (ValueError, OSError, RecursionError) as exc:
                        self._recent_events, integrity = [], (False, 0, f"History unreadable ({type(exc).__name__})")
                    self._recent_key = pending_key
                    if integrity is not None and key == pending_key:
                        self._integrity_key, self._integrity = key, integrity
        unlock = next((i for i in self._recent_events if i.title.startswith("Unlocked")), None)
        self._stat_last.setText(activity.ago(unlock.ts) if unlock else ("None yet" if self._recent_key == key else "Reading"))
        self._recent_list.set_items(self._recent_events[:5], "Reading history..." if self._recent_key != key
                                    else "No activity recorded yet.")
        self._filter_activity()
        if key != self._recent_key or (check_chain and key != self._integrity_key):
            if self._history_pending is None:
                self._history_pending = (key, _HISTORY_WORKER.submit(activity.read_history_snapshot, path, check_chain))
            if not self._history_poll.isActive():
                self._history_poll.start()
            self._integrity_dot.set_tone("idle")
            self._integrity_title.setText("Checking stored records")
            self._integrity_meta.setText("")
            return
        self._history_poll.stop()
        ok, count, message = self._integrity
        self._integrity_dot.set_tone("ok" if ok else "bad")
        self._integrity_title.setText(("Stored records are consistent" if count else "No records to check")
                                      if ok else "History may have been altered")
        self._integrity_meta.setText(
            (f"{count} records, each chained to the one before it." if count else message) if ok else message
        )

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} s"
        minutes, rest = divmod(seconds, 60)
        return f"{minutes} min" if not rest else f"{minutes} min {rest} s"

    # --------------------------------------------------------------- actions
    def _run_lifecycle(self, label, work, done, *, protection_start=False):
        if not self._async_actions:
            done(work())
            return
        self._operation = (_LIFECYCLE_WORKER.submit(work), done)
        self._brand.set_busy(True)
        self._quick.brand.set_busy(True)
        self._apply_lock_state(True)
        self._detail_label.setText(label)
        if protection_start:
            self._status_label.setText("Starting protection")
            self._aperture.set_protection_art("starting")
        else:
            self._aperture.set_protection_art(None)
        self._diagnostic_note.setText(label)
        self._diagnostic_note.show()
        self._quick.note.setText(label)
        for control in (self._stop_button, self._copy_diagnostics_button, self._quick_button,
                        self._quick.primary, *self._quick.presets.values()):
            control.setEnabled(False)
        self._operation_poll.start()

    def _complete_operation(self):
        if self._operation is None:
            return
        future, done = self._operation
        if not future.done():
            return
        self._operation_poll.stop()
        self._operation = None
        self._brand.set_busy(False)
        self._quick.brand.set_busy(False)
        self._copy_diagnostics_button.setEnabled(True)
        try:
            result = future.result()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Protection operation failed")
            result = False
        done(result)

    def _result_note(self, result, failure):
        self._action_note = failure if result is False else ""
        self.refresh()

    def start_protection(self) -> None:
        if self._refuse_while_locked():
            return
        self._run_lifecycle("Starting protection...", self._supervisor.start,
                            lambda ok: self._result_note(ok, "Protection could not start. Check Diagnostics."),
                            protection_start=True)

    def apply_timeout_preset(self, seconds: int) -> None:
        if self._refuse_while_locked():
            return
        self._timeout_input.setValue(int(seconds))
        self.save_idle_timeout()

    def save_idle_timeout(self) -> None:
        if self._refuse_while_locked():
            return
        seconds = self._timeout_input.value()
        self._mark_current_preset()
        if self._supervisor.set_idle_timeout(seconds):
            self._saved_timeout = seconds
            if self._supervisor.status() is RuntimeStatus.RUNNING:
                self._timeout_note.setText("Saved. Active protection updated live (next start also uses this).")
            else:
                self._timeout_note.setText("Saved. Changes apply on the next start.")
            self._stat_timeout.setText(self._format_seconds(seconds))
        else:
            self._timeout_note.setText("The idle timeout could not be saved.")

    def start_reenrollment(self) -> None:
        if self._refuse_while_locked():
            return
        self._run_lifecycle("Opening face setup...", self._supervisor.start_enrollment, self._enrollment_result)

    def _enrollment_result(self, ok):
        self.refresh()
        message = ("Enrollment opened in a separate window." if ok
                   else "Enrollment is already running or unavailable.")
        self._enroll_sub.setText(message)

    def start_over_enrollment(self) -> None:
        if self._refuse_while_locked():
            return
        self._run_lifecycle("Opening face setup...",
                            lambda: self._supervisor.start_enrollment(start_over=True), self._enrollment_result)

    def open_diagnostics(self) -> None:
        try:
            directory = self._supervisor.open_log_directory()
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        except OSError:
            opened = False
        self._action_note = "" if opened else "The local log folder could not be opened."
        self._diagnostic_note.setText(self._action_note)
        self._diagnostic_note.setVisible(not opened)
        if not opened:
            self._detail_label.setText(self._action_note)

    def show_diagnostics(self) -> None:
        self.show_page(len(_PAGES) - 1)
        self.show_center()

    def open_touchpad_settings(self) -> None:
        if self._refuse_while_locked():
            return
        opened = QDesktopServices.openUrl(QUrl("ms-settings:devices-touchpad"))
        if self._touchpad_note is not None:
            self._touchpad_note.setText("Open Windows Settings > Bluetooth & devices > Touchpad.")
            self._touchpad_note.setVisible(not opened)

    def copy_diagnostics(self) -> None:
        if self._operation is not None:
            return
        self._refresh_diagnostics()
        # Share curated fields only. Do not copy paths, logs, enrollment info,
        # process names or IDs, or free-form text from the camera driver.
        status = _STATUS_COPY[self._supervisor.status()][0]
        QApplication.clipboard().setText("\n".join((
            "MajestyGuard diagnostics",
            f"Status: {status}",
            f"Worker RAM: {self._worker_ram.text()}",
            f"App RAM: {self._center_ram.text()}",
            f"Worker CPU: {self._worker_cpu.text()}",
            f"Last face unlock: {self._verify_time.text()}",
            f"Camera start: {self._camera_open_time.text()}",
        )))
        self._action_note = "Diagnostics summary copied."
        self._diagnostic_note.setText(self._action_note)
        self._diagnostic_note.show()

    def stop_protection(self) -> None:
        if self._refuse_while_locked():
            return
        self._run_lifecycle("Stopping protection...", self._supervisor.stop,
                            lambda ok: self._result_note(ok, "Could not stop protection."))

    def _run_primary_action(self) -> None:
        if self._refuse_while_locked():
            return
        enrolled = self._supervisor.enrollment_present()
        status = self._supervisor.status()
        expected = _STATUS_COPY[status][1] if enrolled else "Enroll face"
        if (self._primary_button.text() != expected or status is RuntimeStatus.STARTING
                or getattr(self._supervisor, "enrollment_running", lambda: False)()):
            self.refresh()
            return
        if not enrolled:
            enrollment = getattr(self._supervisor, "start_enrollment", None)
            if callable(enrollment):
                self._run_lifecycle("Opening face setup...", enrollment,
                                    lambda ok: self._result_note(ok, "Face setup could not open."))
            return
        if status is RuntimeStatus.RUNNING:
            self._run_lifecycle("Pausing protection...", self._supervisor.pause,
                                lambda ok: self._result_note(ok, "Could not pause protection. Check Diagnostics."))
        elif status is RuntimeStatus.PAUSED:
            self._run_lifecycle("Resuming protection...", self._supervisor.resume,
                                lambda ok: self._result_note(ok, "Protection could not resume. Check Diagnostics."),
                                protection_start=True)
        else:
            self.start_protection()
            return

    def show_center(self) -> None:
        if self._operation is not None or self._controls_locked():
            return
        self._quick.hide()
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def show_tray(self) -> None:
        self._tray.show()

    def quit_application(self) -> None:
        if self._refuse_while_locked():
            self.show_center()
            return
        if getattr(self._supervisor, "enrollment_running", lambda: False)():
            self._action_note = "Close face setup before quitting MajestyGuard."
            self.refresh()
            self.show_center()
            return
        self._run_lifecycle("Closing MajestyGuard...", self._supervisor.stop, self._finish_quit)

    def _finish_quit(self, stopped):
        if stopped is False:
            self._action_note = "Could not stop protection. MajestyGuard is still open."
            self.refresh()
            return
        self._quitting = True
        self._refresh_timer.stop()
        self._tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._quitting:
            event.accept()
            return
        if self._tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.ignore()
            self.showMinimized()

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            anchor = self._tray.geometry()
            self._present_quick(anchor if anchor.isValid() else QRect(QCursor.pos(), QSize(1, 1)))
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_center()
