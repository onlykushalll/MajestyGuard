"""MajestyGuard Control Center: a local device dashboard.

Lifecycle and settings UI only. It never receives biometric or liveness data;
everything it shows about the camera and history comes from the small
non-biometric files the daemon writes (camera_health.json, audit.log).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
from app.cc_theme import THEMES, stylesheet
from app.cc_widgets import Dot, EventMarker, NavButton, StatusOrb, Surface, Toggle, WorkspaceBackground, make_tray_icon
from app.runtime import RuntimeStatus


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
    ("overview", "Overview", "Your protection at a glance"),
    ("protection", "Protection", "How and when this PC locks"),
    ("camera", "Camera", "Health of the camera MajestyGuard watches"),
    ("activity", "Activity", "Local history of lock events"),
    ("enrollment", "Enrollment", "Your face profile"),
    ("about", "About", "Privacy, shortcuts and files"),
    ("appearance", "Appearance", "Control Center theme"),
)

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
        self._signature: tuple = ()
        self._times: list[QLabel] = []

    def set_items(self, items: list, empty_text: str) -> None:
        signature = tuple((i.ts, i.title, i.detail, i.tone) for i in items) or (("empty", empty_text),)
        if signature == self._signature:
            for label, item in zip(self._times, items):
                label.setText(activity.ago(item.ts))
            return
        self._times = []
        self._signature = signature
        while self._layout.count():
            child = self._layout.takeAt(0)
            if child.widget() is not None:
                child.widget().hide()
                child.widget().deleteLater()
        if not items:
            label = QLabel(empty_text, self)
            label.setObjectName("detail")
            label.setWordWrap(True)
            label.setContentsMargins(2, 10, 2, 10)
            self._layout.addWidget(label)
            return
        for index, item in enumerate(items):
            row = QWidget(self)
            line = QHBoxLayout(row)
            line.setContentsMargins(2, 0, 2, 0)
            line.setSpacing(12)
            line.addWidget(EventMarker(item.tone, first=index == 0, last=index == len(items)-1, parent=row))
            text = QVBoxLayout()
            text.setContentsMargins(0, 9, 0, 9)
            text.setSpacing(1)
            title = QLabel(item.title, row)
            title.setObjectName("rowTitle")
            text.addWidget(title)
            if item.detail:
                meta = QLabel(item.detail, row)
                meta.setObjectName("rowMeta")
                meta.setWordWrap(True)
                text.addWidget(meta)
            line.addLayout(text, 1)
            when = QLabel(activity.ago(item.ts), row)
            when.setObjectName("rowMeta")
            when.setContentsMargins(0, 9, 0, 0)
            self._times.append(when)
            line.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
            self._layout.addWidget(row)


def _card(parent: QWidget, *, radius: float = 22.0, margins=(22, 18, 22, 18), spacing: int = 10):
    panel = Surface(parent, radius=min(radius, 8))
    box = QVBoxLayout(panel)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    return panel, box


def _label(text: str, name: str, parent: QWidget, wrap: bool = False) -> QLabel:
    lab = QLabel(text, parent)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class ControlCenter(QMainWindow):
    """Lifecycle UI only; it never receives biometric or liveness data."""

    def __init__(self, supervisor: SupervisorProtocol, settings_path: Path) -> None:
        super().__init__()
        self._supervisor = supervisor
        self._settings_path = Path(settings_path)
        self._appearance = QSettings(str(self._settings_path.with_name("appearance.ini")), QSettings.Format.IniFormat)
        theme = self._appearance.value("theme", "midnight")
        self.setProperty("ccTheme", theme if isinstance(theme, str) and theme in THEMES else "midnight")
        self._tray_color = None
        self._quitting = False
        self._integrity_key: tuple | None = None
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
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.open_diagnostics)
        for index in range(len(_PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self, activated=lambda i=index: self.show_page(i))

    def _build_window(self) -> None:
        self.setWindowTitle("MajestyGuard")
        self.setMinimumSize(720, 510)
        self.resize(960, 680)
        self.setStyleSheet(stylesheet(THEMES[self.property("ccTheme")]))
        root = WorkspaceBackground(self)
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._sidebar = self._build_sidebar(root)
        outer.addWidget(self._sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(24, 22, 24, 14)
        content.setSpacing(5)
        self._page_title = _label("", "pageTitle", root)
        self._page_sub = _label("", "pageSub", root, True)
        content.addWidget(self._page_title)
        content.addWidget(self._page_sub)
        content.addSpacing(10)
        self._stack = QStackedWidget(root)
        content.addWidget(self._stack, 1)
        outer.addLayout(content, 1)

        for builder in (self._page_overview, self._page_protection, self._page_camera,
                        self._page_activity, self._page_enrollment, self._page_about, self._page_appearance):
            scroll = QScrollArea(self._stack)
            scroll.setObjectName("pageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(builder())
            self._stack.addWidget(scroll)
        self.show_page(0)

    def _build_sidebar(self, parent: QWidget) -> QWidget:
        side, box = _card(parent, margins=(12, 21, 12, 14), spacing=2)
        side.setObjectName("sidebar")
        side.setFixedWidth(182)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(9)
        mark = QLabel(side)
        mark.setFixedSize(34, 38)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(make_tray_icon("#C1C8BD").pixmap(QSize(32, 32), side.devicePixelRatioF()))
        mark.setToolTip("MajestyGuard Control Center")
        brand_row.addWidget(mark)
        self._brand_text = QWidget(side)
        brand = QVBoxLayout(self._brand_text)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(0)
        brand.addWidget(_label("MajestyGuard", "brand", side))
        brand.addWidget(_label("Control Center", "brandSub", side))
        brand_row.addWidget(self._brand_text)
        brand_row.addStretch(1)
        box.addLayout(brand_row)
        box.addSpacing(20)

        self._nav: list[NavButton] = []
        for index, (kind, title, _sub) in enumerate(_PAGES):
            if kind == "about":
                box.addSpacing(8)
                rule = QFrame(side)
                rule.setObjectName("hair")
                box.addWidget(rule)
                box.addSpacing(8)
            btn = NavButton(kind, title, side)
            btn.setToolTip(f"{title}  (Ctrl+{index + 1})")
            btn.clicked.connect(lambda _c=False, i=index: self.show_page(i))
            self._nav.append(btn)
            box.addWidget(btn)
        box.addStretch(1)
        self._device_caption = _label("This device", "rowMeta", side)
        box.addWidget(self._device_caption)
        self._side_pill = _label("", "pill", side)
        box.addWidget(self._side_pill, 0, Qt.AlignmentFlag.AlignLeft)
        self._compact_status = Dot(parent=side)
        box.addWidget(self._compact_status, 0, Qt.AlignmentFlag.AlignHCenter)
        self._compact_status.hide()
        return side

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_sidebar"):
            return
        compact = self.width() < 840
        if compact == self._nav[0].compact:
            return
        self._sidebar.setFixedWidth(64 if compact else 182)
        for nav in self._nav:
            nav.compact = compact
            nav.update()
        for label in (self._brand_text, self._device_caption, self._side_pill):
            label.setVisible(not compact)
        self._compact_status.setVisible(compact)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        page.setObjectName("pageContent")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)
        return page, box

    def _page_overview(self) -> QWidget:
        page, box = self._page()
        hero, hero_box = _card(page, margins=(24, 19, 24, 18))
        hero.setObjectName("statusBand")
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(5)
        copy.addWidget(_label("Protection status", "statLabel", hero))
        self._status_label = _label("", "status", hero, True)
        copy.addWidget(self._status_label)
        self._detail_label = _label("", "detail", hero, True)
        copy.addWidget(self._detail_label)
        self._primary_button = QPushButton(hero)
        self._primary_button.setObjectName("primary")
        self._primary_button.clicked.connect(self._run_primary_action)
        copy.addSpacing(6)
        copy.addWidget(self._primary_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(copy, 1)
        self._orb_widget = StatusOrb(112, hero)
        row.addWidget(self._orb_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        hero_box.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._gallery_chip, self._lock_chip, self._perf_chip = (
            _label("", "rowTitle", hero, True) for _ in range(3)
        )
        for caption, chip in (("Face profile", self._gallery_chip), ("Session", self._lock_chip),
                              ("Last cold start", self._perf_chip)):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(_label(caption, "rowMeta", hero))
            column.addWidget(chip)
            chips.addLayout(column, 1)
        hero_box.addSpacing(6)
        rule = QFrame(hero)
        rule.setObjectName("hair")
        hero_box.addWidget(rule)
        hero_box.addLayout(chips)
        box.addWidget(hero)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._stat_timeout = self._stat_card(page, stats, "IDLE CHECK", 1)
        self._stat_camera = self._stat_card(page, stats, "CAMERA", 2)
        self._stat_last = self._stat_card(page, stats, "LAST UNLOCK", 3)
        box.addLayout(stats)

        recent = QWidget(page)
        recent_box = QVBoxLayout(recent)
        recent_box.setContentsMargins(2, 4, 2, 0)
        recent_box.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(_label("Recent activity", "cardTitle", recent))
        top.addStretch(1)
        more = QPushButton("View all", recent)
        more.setObjectName("link")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(lambda: self.show_page(3))
        top.addWidget(more)
        recent_box.addLayout(top)
        self._recent_list = ActivityList(recent)
        recent_box.addWidget(self._recent_list)
        recent_box.addStretch(1)
        box.addWidget(recent, 1)
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

        win, win_box = _card(page)
        head = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label("Allow Windows lock", "cardTitle", win))
        text.addWidget(_label("Lets MajestyGuard lock the Windows session itself, which is what TAB on the lock "
                              "screen does after you confirm it. Changing this restarts protection.",
                              "detail", win, True))
        head.addLayout(text, 1)
        head.addSpacing(18)
        self._windows_lock_toggle = Toggle(win)
        self._windows_lock_toggle.setAccessibleName("Allow Windows lock")
        self._windows_lock_toggle.clicked.connect(self._on_windows_lock_clicked)
        head.addWidget(self._windows_lock_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        win_box.addLayout(head)
        box.addWidget(win)

        keys, keys_box = _card(page)
        keys_box.addWidget(_label("On the lock screen", "cardTitle", keys))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for r, (key, title, note) in enumerate((
            ("SPACE", "Verify your face", "Look at the camera. Failed attempts wait a little longer each time."),
            ("TAB", "Windows lock", "Press TAB, then TAB again to confirm, to sign in with your Windows password."),
        )):
            cap = _label(key, "keycap", keys)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setMinimumWidth(72)
            grid.addWidget(cap, r, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(_label(title, "rowTitle", keys))
            col.addWidget(_label(note, "rowMeta", keys, True))
            grid.addLayout(col, r, 1)
        grid.setColumnStretch(1, 1)
        keys_box.addLayout(grid)
        box.addWidget(keys)
        box.addStretch(1)
        self._mark_current_preset()
        return page

    def _page_camera(self) -> QWidget:
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

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._cam_backend = self._stat_card(page, stats, "BACKEND")
        self._cam_recoveries = self._stat_card(page, stats, "SELF-HEALED")
        self._cam_retry = self._stat_card(page, stats, "NEXT RETRY")
        box.addLayout(stats)

        events, ebox = _card(page, margins=(22, 16, 22, 12), spacing=4)
        ebox.addWidget(_label("What the camera has been doing", "cardTitle", events))
        self._camera_events = _label("", "mono", events, True)
        ebox.addWidget(self._camera_events)
        ebox.addStretch(1)
        box.addWidget(events, 1)
        return page

    def _page_activity(self) -> QWidget:
        page, box = self._page()
        banner, bbox = _card(page, radius=20, margins=(20, 14, 20, 14))
        row = QHBoxLayout()
        row.setSpacing(12)
        self._integrity_dot = Dot("ok", 12, banner)
        row.addWidget(self._integrity_dot)
        col = QVBoxLayout()
        col.setSpacing(1)
        self._integrity_title = _label("Local history", "rowTitle", banner)
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
        self._activity_list = ActivityList(card)
        cbox.addWidget(self._activity_list)
        cbox.addStretch(1)
        box.addWidget(card, 1)
        return page

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

    def _page_about(self) -> QWidget:
        page, box = self._page()
        priv, pbox = _card(page)
        pbox.addWidget(_label("Private by design", "cardTitle", priv))
        pbox.addWidget(_label("Your face template is encrypted with Windows DPAPI and stays on this PC. The camera "
                              "is only used for verification, and no video or images are stored or sent anywhere. "
                              "This window shows lifecycle and settings only, never biometric data.", "detail", priv, True))
        box.addWidget(priv)
        files, fbox = _card(page)
        fbox.addWidget(_label("Where things live", "cardTitle", files))
        directory = QLineEdit(str(self._state_dir()), files)
        directory.setReadOnly(True)
        directory.setObjectName("folderPath")
        directory.setAccessibleName("Local data folder")
        directory.setToolTip(directory.text())
        directory.setCursorPosition(0)
        fbox.addWidget(directory)
        self._diag_btn = QPushButton("Open diagnostics", files)
        self._diag_btn.setObjectName("secondary")
        self._diag_btn.clicked.connect(self.open_diagnostics)
        fbox.addWidget(self._diag_btn, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(files)
        keys, kbox = _card(page)
        kbox.addWidget(_label("Keyboard shortcuts", "cardTitle", keys))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for r, (combo, what) in enumerate((("Ctrl + 1 to 7", "Switch tab"), ("Ctrl + R", "Re-enroll face"),
                                           ("Ctrl + D", "Open diagnostics"))):
            cap = _label(combo, "keycap", keys)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(cap, r, 0)
            grid.addWidget(_label(what, "detail", keys), r, 1)
        grid.setColumnStretch(2, 1)
        kbox.addLayout(grid)
        box.addWidget(keys)
        box.addStretch(1)
        return page

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
        box.addStretch(1)
        return page

    def _select_theme(self, key: str) -> None:
        if key == self.property("ccTheme") or key not in THEMES:
            return
        self.setProperty("ccTheme", key)
        self.setStyleSheet(stylesheet(THEMES[key]))
        for child in self.findChildren(QWidget):
            child.update()
        self._appearance.setValue("theme", key)
        self._appearance.sync()
        if self._appearance.status() != QSettings.Status.NoError:
            self._theme_note.setText("Applied for this session. Could not save the theme.")

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(make_tray_icon("#7f8b9c"), self)
        self._tray.setToolTip("MajestyGuard")
        menu = QMenu(self)
        self._tray_primary_action = QAction(self)
        self._tray_primary_action.triggered.connect(self._run_primary_action)
        menu.addAction(self._tray_primary_action)
        for text, slot in (("Open Control Center", self.show_center), ("Re-enroll face", self.start_reenrollment),
                           ("Open diagnostics", self.open_diagnostics)):
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
        _kind, title, sub = _PAGES[index]
        self._page_title.setText(title)
        self._page_sub.setText(sub)
        self._refresh_pages()

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
                       self._save_timeout_button, *self._preset_buttons):
            widget.setEnabled(not locked)
        has_toggle = callable(getattr(self._supervisor, "set_windows_lock_enabled", None))
        self._windows_lock_toggle.setEnabled(has_toggle and not locked)
        if locked:
            self._primary_button.setEnabled(False)
            self._tray_primary_action.setEnabled(False)
            self._detail_label.setText(_LOCKED_NOTE)

    def _refuse_while_locked(self) -> bool:
        if not self._controls_locked():
            return False
        self._timeout_note.setText(_LOCKED_NOTE)
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
        self._status_label.setText(text)
        self._detail_label.setText(detail)
        self._primary_button.setText(action)
        self._primary_button.setEnabled(status is not RuntimeStatus.STARTING)
        self._tray_primary_action.setText(action)
        self._tray_primary_action.setEnabled(status is not RuntimeStatus.STARTING)
        self._orb_widget.set_state_visual(color, animated=enrolled and status is RuntimeStatus.STARTING,
                                          symbol=status.name.lower() if enrolled else "enroll")
        self._side_pill.setText(pill)
        self._compact_status.set_tone(tone)
        self._compact_status.setAccessibleName(pill)
        self._compact_status.setToolTip(pill)
        if color != self._tray_color:
            self.setProperty("statusInk", color)
            self._orb_widget.parentWidget().update()
            self._tray.setIcon(make_tray_icon(color))
            self._tray_color = color
        self._gallery_text = self._refresh_telemetry_chips(enrolled)
        self._enrolled = enrolled
        self._refresh_pages()
        self._apply_lock_state(self._controls_locked())

    def _refresh_pages(self) -> None:
        """Fill the data-driven parts of every tab (cheap: tail reads, cached integrity)."""
        state = self._state_dir()
        enrolled = self._enrolled if hasattr(self, "_enrolled") else self._supervisor.enrollment_present()
        recent = activity.read_recent(state / "audit.log", 40)

        self._stat_timeout.setText(self._format_seconds(self._saved_timeout))
        unlock = next((i for i in recent if i.title.startswith("Unlocked")), None)
        self._stat_last.setText(activity.ago(unlock.ts) if unlock else "None yet")
        self._recent_list.set_items(recent[:5], "Nothing yet. Locks, unlocks and camera events will show up here.")
        self._activity_list.set_items(recent, "Nothing yet. Locks, unlocks and camera events will show up here.")
        self._refresh_integrity(state / "audit.log")

        cam = activity.read_camera_health(state / "camera_health.json")
        live = cam is not None and cam.age_s < 60
        if cam is None or not live:
            self._camera_dot.set_tone("idle")
            self._camera_headline.setText("No live camera data")
            self._camera_hint.setText("Camera status appears here while protection is running.")
            self._stat_camera.setText("Idle")
            self._cam_backend.setText(cam.backend if cam else "--")
            self._cam_recoveries.setText(str(cam.recoveries) if cam else "--")
            self._cam_retry.setText("--")
            self._camera_events.setText("")
            self._nav[2].set_badge("")
        else:
            healthy = cam.status == "healthy"
            tone = "ok" if healthy else ("bad" if cam.status == "failed" else "warn")
            self._camera_dot.set_tone(tone)
            self._camera_headline.setText(cam.headline or ("Camera is healthy" if healthy else "Camera needs attention"))
            self._camera_hint.setText(cam.hint or ("Frames are arriving normally." if healthy else "Retrying automatically."))
            self._stat_camera.setText("Healthy" if healthy else cam.status.capitalize())
            self._cam_backend.setText(cam.backend)
            self._cam_recoveries.setText(str(cam.recoveries))
            self._cam_retry.setText(f"{cam.next_retry_s:.0f} s" if cam.next_retry_s > 0.5 else "--")
            self._camera_events.setText("\n".join(cam.events) if cam.events else "No events recorded.")
            self._nav[2].set_badge("" if healthy else "!", tone)

        gallery = getattr(self, "_gallery_text", "")
        outcome = activity.read_enroll_result(state)
        self._enroll_result.setText(outcome or "")
        self._enroll_result.setVisible(outcome is not None)
        if enrolled:
            self._enroll_dot.set_tone("ok")
            self._enroll_title.setText("Face profile ready")
            self._enroll_sub.setText(f"{gallery}. Re-enroll if your look changes a lot or recognition feels off.")
            self._reenroll_btn.setText("Re-enroll face")
            self._nav[4].set_badge("")
        else:
            self._enroll_dot.set_tone("warn")
            self._enroll_title.setText("No face profile yet")
            self._enroll_sub.setText("Create a local face profile to switch protection on.")
            self._reenroll_btn.setText("Enroll face")
            self._nav[4].set_badge("!", "warn")

        getter = getattr(self._supervisor, "windows_lock_enabled", None)
        if callable(getter):
            try:
                self._windows_lock_toggle.setChecked(bool(getter()))
            except Exception:
                pass

    def _refresh_integrity(self, path: Path) -> None:
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key != self._integrity_key or key is None:
            self._integrity_key = key
            self._integrity = activity.verify_integrity(path)
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
    def start_protection(self) -> None:
        self._supervisor.start()
        self.refresh()

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
            self._timeout_note.setText("That timeout is outside the permitted range.")

    def _on_windows_lock_clicked(self, checked: bool) -> None:
        setter = getattr(self._supervisor, "set_windows_lock_enabled", None)
        if self._refuse_while_locked() or not callable(setter):
            self._windows_lock_toggle.setChecked(not checked)
            return
        if not setter(bool(checked)):
            self._windows_lock_toggle.setChecked(not checked)
            self._timeout_note.setText("Could not change the Windows lock setting.")
        self.refresh()

    def start_reenrollment(self) -> None:
        if self._refuse_while_locked():
            return
        if self._supervisor.start_enrollment():
            self._enroll_sub.setText("Enrollment opened in a separate window.")
            self._timeout_note.setText("Enrollment opened in a separate window.")
        else:
            self._enroll_sub.setText("Enrollment is already running or unavailable.")
            self._timeout_note.setText("Enrollment is already running or unavailable.")

    def start_over_enrollment(self) -> None:
        if self._refuse_while_locked():
            return
        if self._supervisor.start_enrollment(start_over=True):
            self._enroll_sub.setText("Enrollment opened in a separate window.")
        else:
            self._enroll_sub.setText("Enrollment is already running or unavailable.")

    def open_diagnostics(self) -> None:
        directory = self._supervisor.open_log_directory()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _run_primary_action(self) -> None:
        if self._refuse_while_locked():
            return
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
        if self._refuse_while_locked():
            self.show_center()
            return
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
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_center()
