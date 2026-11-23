"""MajestyGuard Control Center: a tabbed, local-only dashboard with switchable colour themes.

Lifecycle and settings UI only. It never receives biometric or liveness data;
everything it shows about the camera and history comes from the small
non-biometric files the daemon writes (camera_health.json, audit.log).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app import activity
from app import theme as theme_mod
from app.cc_widgets import (
    Backdrop,
    Dot,
    GradientCard,
    HeroBadge,
    IconChip,
    LogoMark,
    NavButton,
    StatusRing,
    Surface,
    ThemeSwatch,
    Toggle,
    make_tray_icon,
)
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
    RuntimeStatus.STOPPED: ("Protection stopped", "Start protection", "idle"),
    RuntimeStatus.STARTING: ("Starting protection", "Starting", "info"),
    RuntimeStatus.RUNNING: ("Protection running", "Pause protection", "ok"),
    RuntimeStatus.PAUSED: ("Protection paused", "Resume protection", "idle"),
    RuntimeStatus.DEGRADED: ("Protection degraded", "Restart protection", "warn"),
}
# Hero gradients are state colours (not theme colours) so "running" always reads green,
# "needs attention" amber and "failed" red, whichever theme is active.
_HERO = {
    "ok": ("#14B879", "#1593DB"),
    "info": ("#3B7BFF", "#7A4DFF"),
    "warn": ("#FF9F2E", "#FF5A3C"),
    "bad": ("#F0405A", "#A81E62"),
    "idle": ("#5E6985", "#394159"),
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
    ("activity", "Activity", "A tamper-evident history of lock events"),
    ("enrollment", "Enrollment", "Your face profile"),
    ("appearance", "Appearance", "Pick a colour theme"),
    ("about", "About", "Privacy, shortcuts and files"),
)
_APPEARANCE_FILE = "appearance.json"

class ActivityList(QWidget):
    """Rows of (dot, title + note, time). Rebuilt only when the content changes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._signature: tuple = ()

    def set_items(self, items: list, empty_text: str) -> None:
        signature = tuple((i.ts, i.title, i.detail) for i in items) or (("empty", empty_text),)
        if signature == self._signature:
            return
        self._signature = signature
        while self._layout.count():
            child = self._layout.takeAt(0)
            if child.widget() is not None:
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
            line.setContentsMargins(2, 9, 2, 9)
            line.setSpacing(12)
            line.addWidget(Dot(item.tone, parent=row), 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(1)
            title = QLabel(item.title, row)
            title.setObjectName("rowTitle")
            text.addWidget(title)
            if item.detail:
                meta = QLabel(item.detail, row)
                meta.setObjectName("rowMeta")
                text.addWidget(meta)
            line.addLayout(text, 1)
            when = QLabel(activity.ago(item.ts), row)
            when.setObjectName("rowMeta")
            line.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
            self._layout.addWidget(row)
            if index < len(items) - 1:
                rule = QFrame(self)
                rule.setObjectName("hair")
                self._layout.addWidget(rule)


def _card(parent: QWidget, *, radius: float = 22.0, margins=(22, 18, 22, 18), spacing: int = 10):
    panel = Surface(parent, radius=radius)
    box = QVBoxLayout(panel)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    return panel, box


def _hero(parent: QWidget, *, radius: float = 28.0, margins=(26, 22, 26, 22), spacing: int = 8):
    card = GradientCard(parent, radius=radius)
    box = QVBoxLayout(card)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    return card, box


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
        self._quitting = False
        self._integrity_key: tuple | None = None
        self._integrity: tuple[bool, int, str] = (True, 0, "")
        theme_mod.set_active(theme_mod.load_choice(self._settings_path.parent / _APPEARANCE_FILE))
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
        self.setMinimumSize(940, 640)
        self.resize(1040, 700)
        self.setStyleSheet(theme_mod.build_style(theme_mod.active()))
        root = Backdrop(self)
        self._backdrop = root
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(18)
        outer.addWidget(self._build_sidebar(root))

        content = QVBoxLayout()
        content.setSpacing(0)
        eyebrow = _label("MAJESTYGUARD CONTROL CENTER", "eyebrow", root)
        content.addWidget(eyebrow)
        self._page_title = _label("", "pageTitle", root)
        self._page_title.setContentsMargins(0, 3, 0, 0)
        content.addWidget(self._page_title)
        self._page_sub = _label("", "pageSub", root)
        content.addWidget(self._page_sub)
        content.addSpacing(16)
        self._stack = QStackedWidget(root)
        content.addWidget(self._stack, 1)
        outer.addLayout(content, 1)

        for builder in (self._page_overview, self._page_protection, self._page_camera,
                        self._page_activity, self._page_enrollment, self._page_appearance, self._page_about):
            self._stack.addWidget(builder())
        self.show_page(0)

    def _build_sidebar(self, parent: QWidget) -> QWidget:
        side, box = _card(parent, radius=26, margins=(14, 18, 14, 14), spacing=4)
        side.setFixedWidth(236)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        head.setSpacing(10)
        head.addWidget(LogoMark(42, side))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_label("MajestyGuard", "brand", side))
        names.addWidget(_label("Control Center", "brandSub", side))
        head.addLayout(names, 1)
        box.addLayout(head)
        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(6, 8, 6, 12)
        self._side_pill = _label("", "pill", side)
        pill_row.addWidget(self._side_pill)
        pill_row.addStretch(1)
        box.addLayout(pill_row)

        self._nav: list[NavButton] = []
        for index, (kind, title, _sub) in enumerate(_PAGES):
            btn = NavButton(kind, title, side)
            btn.setToolTip(f"{title}  (Ctrl+{index + 1})")
            btn.clicked.connect(lambda _c=False, i=index: self.show_page(i))
            self._nav.append(btn)
            box.addWidget(btn)
        box.addStretch(1)
        box.addWidget(_label("Camera and face decisions stay inside the local daemon. Nothing leaves this PC.",
                             "detail", side, wrap=True))
        return side

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)
        return page, box

    def _page_overview(self) -> QWidget:
        page, box = self._page()
        hero, hero_box = _hero(page)
        self._hero = hero
        row = QHBoxLayout()
        row.setSpacing(18)
        self._orb_widget = StatusRing(72, hero)
        row.addWidget(self._orb_widget)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self._status_label = _label("", "heroTitle", hero)
        copy.addWidget(self._status_label)
        self._detail_label = _label("Camera and biometric decisions stay inside the local daemon.", "heroSub", hero, True)
        copy.addWidget(self._detail_label)
        row.addLayout(copy, 1)
        self._primary_button = QPushButton(hero)
        self._primary_button.setObjectName("hero")
        self._primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary_button.setMinimumWidth(190)
        self._primary_button.clicked.connect(self._run_primary_action)
        row.addWidget(self._primary_button, 0, Qt.AlignmentFlag.AlignVCenter)
        hero_box.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._gallery_chip = _label("Gallery: --", "chip", hero)
        self._lock_chip = _label("Session: UNLOCKED", "chip", hero)
        self._perf_chip = _label("Engine: Ready", "chip", hero)
        for chip in (self._gallery_chip, self._lock_chip, self._perf_chip):
            chips.addWidget(chip)
        chips.addStretch(1)
        hero_box.addSpacing(6)
        hero_box.addLayout(chips)
        box.addWidget(hero)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._stat_timeout = self._stat_card(page, stats, "LOCKS AFTER", "clock")
        self._stat_camera = self._stat_card(page, stats, "CAMERA", "camera")
        self._stat_last = self._stat_card(page, stats, "LAST UNLOCK", "unlock")
        box.addLayout(stats)

        recent, recent_box = _card(page, margins=(22, 16, 22, 10), spacing=2)
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

    def _stat_card(self, parent: QWidget, row: QHBoxLayout, caption: str, icon: str = "overview") -> QLabel:
        card, box = _card(parent, radius=20, margins=(18, 14, 18, 14), spacing=0)
        line = QHBoxLayout()
        line.setSpacing(14)
        line.addWidget(IconChip(icon, 42, card), 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(_label(caption, "statLabel", card))
        value = _label("--", "value", card)
        col.addWidget(value)
        line.addLayout(col, 1)
        box.addLayout(line)
        row.addWidget(card, 1)
        return value

    def _page_protection(self) -> QWidget:
        page, box = self._page()
        idle, idle_box = _card(page)
        idle_box.addWidget(_label("Lock after inactivity", "cardTitle", idle))
        idle_box.addWidget(_label("How long the PC can sit idle before the lock screen appears.", "detail", idle, True))
        row = QHBoxLayout()
        row.setSpacing(10)
        self._timeout_input = QSpinBox(idle)
        self._timeout_input.setRange(15, 1800)
        self._timeout_input.setSuffix(" seconds")
        self._timeout_input.setMinimumWidth(150)
        self._timeout_input.setValue(self._current_idle_timeout())
        self._timeout_input.valueChanged.connect(lambda _v: self._mark_current_preset())
        row.addWidget(self._timeout_input)
        self._save_timeout_button = QPushButton("Save", idle)
        self._save_timeout_button.setObjectName("secondary")
        self._save_timeout_button.clicked.connect(self.save_idle_timeout)
        row.addWidget(self._save_timeout_button)
        row.addSpacing(12)
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
        card, cbox = _hero(page)
        self._camera_hero = card
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(HeroBadge("camera", 60, card), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._camera_headline = _label("No camera data yet", "heroTitle", card)
        text.addWidget(self._camera_headline)
        self._camera_hint = _label("Camera status appears here while protection is running.", "heroSub", card, True)
        text.addWidget(self._camera_hint)
        row.addLayout(text, 1)
        cbox.addLayout(row)
        box.addWidget(card)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._cam_backend = self._stat_card(page, stats, "BACKEND", "protection")
        self._cam_recoveries = self._stat_card(page, stats, "SELF-HEALED", "activity")
        self._cam_retry = self._stat_card(page, stats, "NEXT RETRY", "clock")
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
        self._integrity_title = _label("History is intact", "rowTitle", banner)
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
        card, cbox = _hero(page)
        self._enroll_hero = card
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(HeroBadge("enrollment", 60, card), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._enroll_title = _label("Face profile ready", "heroTitle", card)
        self._enroll_sub = _label("", "heroSub", card, True)
        text.addWidget(self._enroll_title)
        text.addWidget(self._enroll_sub)
        row.addLayout(text, 1)
        self._reenroll_btn = QPushButton("Re-enroll face", card)
        self._reenroll_btn.setObjectName("hero")
        self._reenroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reenroll_btn.setMinimumWidth(170)
        self._reenroll_btn.clicked.connect(self.start_reenrollment)
        row.addWidget(self._reenroll_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        cbox.addLayout(row)
        box.addWidget(card)
        tips, tbox = _card(page)
        tbox.addWidget(_label("For a profile that works everywhere", "cardTitle", tips))
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

    def _page_appearance(self) -> QWidget:
        page, box = self._page()
        card, cbox = _card(page, margins=(24, 20, 24, 22), spacing=12)
        cbox.addWidget(_label("Colour theme", "cardTitle", card))
        cbox.addWidget(_label("Applies instantly and is remembered. The lock screen keeps its own look.",
                              "detail", card, True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self._theme_swatches: list[ThemeSwatch] = []
        for i, preset in enumerate(theme_mod.PRESETS):
            sw = ThemeSwatch(preset, card)
            sw.setChecked(preset.key == theme_mod.active().key)
            sw.clicked.connect(lambda _c=False, k=preset.key: self.apply_theme(k))
            self._theme_swatches.append(sw)
            grid.addWidget(sw, 0, i)
            grid.setColumnStretch(i, 1)
        cbox.addLayout(grid)
        self._theme_note = _label("", "detail", card, True)
        cbox.addWidget(self._theme_note)
        box.addWidget(card)
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
        fbox.addWidget(_label(str(self._state_dir()), "mono", files, True))
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
            return False

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
        for widget in (self._reenroll_btn, self._timeout_input, self._save_timeout_button, *self._preset_buttons):
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
        gallery = "Not enrolled"
        if enrolled:
            gallery = "Enrolled (DPAPI)"
            meta_file = state_dir / "embeddings_v2_meta.json"
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    gallery = f"{meta.get('selected_count', '?')} templates · {str(meta.get('profile', 'robust')).capitalize()}"
                except Exception:
                    pass
        self._gallery_chip.setText(f"Gallery: {gallery}")

        lock_file = state_dir / "lock_state.txt"
        try:
            lock_state = lock_file.read_text(encoding="utf-8").strip() if lock_file.is_file() else "UNLOCKED"
        except OSError:
            lock_state = "UNLOCKED"
        self._lock_chip.setText(f"Session: {lock_state or 'UNLOCKED'}")

        perf_text = "Engine: Ready"
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
                        perf_text = f"Cold start: {line.split('total=', 1)[1].split()[0]}"
                        break
            except OSError:
                pass
        self._perf_chip.setText(perf_text)
        return gallery

    def refresh(self) -> None:
        enrolled = self._supervisor.enrollment_present()
        status = self._supervisor.status()
        if not enrolled:
            text, action, tone_key = "Enrollment required", "Enroll face", "warn"
            detail = "Create a local face profile before starting protection."
            pill, tone = "Enroll to start", "warn"
        else:
            text, action, tone_key = _STATUS_COPY[status]
            detail = "Camera and biometric decisions stay inside the local daemon."
            pill, tone = _SIDE_PILL[status]
        self._status_label.setText(text)
        self._detail_label.setText(detail)
        self._primary_button.setText(action)
        self._primary_button.setEnabled(status is not RuntimeStatus.STARTING)
        self._tray_primary_action.setText(action)
        self._tray_primary_action.setEnabled(status is not RuntimeStatus.STARTING)
        animated = status in (RuntimeStatus.RUNNING, RuntimeStatus.STARTING)
        hero_a, hero_b = _HERO[tone_key]
        self._hero.set_colors(hero_a, hero_b)
        self._orb_widget.set_state_visual(hero_a, animated=animated)
        color = hero_a
        self._side_pill.setText(pill)
        self._tray.setIcon(make_tray_icon(color))
        self._gallery_text = self._refresh_telemetry_chips(enrolled)
        self._enrolled = enrolled
        self._refresh_pages()
        self._apply_lock_state(self._controls_locked())

    def _refresh_pages(self) -> None:
        """Fill the data-driven parts of every tab (cheap: tail reads, cached integrity)."""
        state = self._state_dir()
        enrolled = getattr(self, "_enrolled", self._supervisor.enrollment_present())
        recent = activity.read_recent(state / "audit.log", 40)

        self._stat_timeout.setText(self._format_seconds(self._timeout_input.value()))
        unlock = next((i for i in recent if i.title.startswith("Unlocked")), None)
        self._stat_last.setText(activity.ago(unlock.ts) if unlock else "None yet")
        self._recent_list.set_items(recent[:5], "Nothing yet. Locks, unlocks and camera events will show up here.")
        self._activity_list.set_items(recent, "Nothing yet. Locks, unlocks and camera events will show up here.")
        self._refresh_integrity(state / "audit.log")

        cam = activity.read_camera_health(state / "camera_health.json")
        live = cam is not None and cam.age_s < 60
        if cam is None or not live:
            self._camera_hero.set_colors(*_HERO["idle"])
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
            self._camera_hero.set_colors(*_HERO[tone])
            self._camera_headline.setText(cam.headline or ("Camera is healthy" if healthy else "Camera needs attention"))
            self._camera_hint.setText(cam.hint or ("Frames are arriving normally." if healthy else "Retrying automatically."))
            self._stat_camera.setText("Healthy" if healthy else cam.status.capitalize())
            self._cam_backend.setText(cam.backend)
            self._cam_recoveries.setText(str(cam.recoveries))
            self._cam_retry.setText(f"{cam.next_retry_s:.0f} s" if cam.next_retry_s > 0.5 else "--")
            self._camera_events.setText("\n".join(cam.events) if cam.events else "No events recorded.")
            self._nav[2].set_badge("" if healthy else "!", tone)

        gallery = getattr(self, "_gallery_text", "")
        if enrolled:
            self._enroll_hero.set_colors(*_HERO["ok"])
            self._enroll_title.setText("Face profile ready")
            self._enroll_sub.setText(f"{gallery}. Re-enroll if your look changes a lot or recognition feels off.")
            self._reenroll_btn.setText("Re-enroll face")
            self._nav[4].set_badge("")
        else:
            self._enroll_hero.set_colors(*_HERO["warn"])
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
        self._integrity_title.setText("History is intact" if ok else "History may have been altered")
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
    def apply_theme(self, key: str) -> None:
        chosen = theme_mod.set_active(theme_mod.by_key(key))
        self.setStyleSheet(theme_mod.build_style(chosen))
        for swatch in self._theme_swatches:
            swatch.setChecked(swatch.theme.key == chosen.key)
            swatch.update()
        saved = theme_mod.save_choice(self._state_dir() / _APPEARANCE_FILE, chosen)
        self._theme_note.setText(f"{chosen.label} applied." if saved else f"{chosen.label} applied for this session.")
        self._backdrop.invalidate()
        for child in self.findChildren(QWidget):
            child.update()

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
