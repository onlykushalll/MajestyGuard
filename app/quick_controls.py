"""The notification-area panel. All protection commands belong to its owner."""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.aperture import Aperture
from app.cc_widgets import TransportButton
from app.cc_theme import reduced_motion
from ui.brand import BrandTrace


def popup_position(anchor: QRect, work: QRect, size: QSize) -> QPoint:
    """Pick the side facing into the work area, including left/top taskbars."""
    margin = 8
    x = anchor.right() - size.width() + 1
    y = anchor.top() - margin - size.height()
    if y < work.top() + margin:
        y = anchor.bottom() + margin + 1
    x = max(work.left(), min(x, work.right() - size.width() + 1))
    y = max(work.top(), min(y, work.bottom() - size.height() + 1))
    return QPoint(x, y)


class QuickControls(QFrame):
    primaryRequested = pyqtSignal()
    destinationRequested = pyqtSignal(str)
    timeoutRequested = pyqtSignal(int)

    def __init__(self, owner: QWidget):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("quickControls")
        self.setWindowTitle("MajestyGuard quick controls")
        self.setAccessibleName(self.windowTitle())
        self.setFixedWidth(340)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 14)
        box.setSpacing(10)
        header = QHBoxLayout()
        self.brand = BrandTrace(self, size=28, ink="#718C9D")
        header.addWidget(self.brand)
        header.addWidget(self._label("MajestyGuard", "brand"), 1)
        header.addWidget(self._label("Quick controls", "rowMeta"))
        box.addLayout(header)
        plate = QFrame(self)
        plate.setObjectName("aperturePlate")
        main = QHBoxLayout(plate)
        main.setContentsMargins(10, 10, 12, 10)
        self.aperture = Aperture(plate, miniature=True)
        main.addWidget(self.aperture)
        copy = QVBoxLayout()
        self.headline = self._label("", "quickPlateTitle")
        self.headline.setWordWrap(True)
        copy.addWidget(self.headline)
        self.note = self._label("", "plateDetail")
        self.note.setWordWrap(True)
        copy.addWidget(self.note)
        main.addLayout(copy, 1)
        box.addWidget(plate)
        self.primary = TransportButton(self)
        self.primary.setObjectName("primary")
        self.primary.clicked.connect(self.primaryRequested)
        box.addWidget(self.primary)
        self._rule(box)
        self.routes = {}
        self.readings = {}
        self.presets = {}
        for key, caption in (("activity", "Camera & activity"), ("protection", "Idle check"),
                             ("diagnostics", "Diagnostics")):
            button = QPushButton(self)
            button.setObjectName("quickRoute")
            row = QHBoxLayout(button)
            row.setContentsMargins(10, 0, 10, 0)
            left = self._label(caption, "rowTitle")
            right = self._label("", "rowMeta")
            for label in (left, right):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(left, 1)
            row.addWidget(right)
            arrow = self._label("\u203a", "quickArrow")
            arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(arrow)
            button.setMinimumHeight(34)
            button.setAccessibleName(caption)
            button.clicked.connect(lambda _, k=key: self.destinationRequested.emit(k))
            box.addWidget(button)
            self.routes[key], self.readings[key] = button, right
            if key == "protection":
                choices = QHBoxLayout()
                choices.setSpacing(5)
                for seconds, caption in ((30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")):
                    preset = QPushButton(caption, self)
                    preset.setObjectName("filter")
                    preset.setCheckable(True)
                    preset.setAccessibleName(f"Set idle check to {seconds} seconds")
                    preset.setToolTip(preset.accessibleName())
                    preset.clicked.connect(lambda _, s=seconds: self.timeoutRequested.emit(s))
                    self.presets[seconds] = preset
                    choices.addWidget(preset, 1)
                box.addLayout(choices)
        self.open_center = QPushButton("Open Control Center", self)
        self.open_center.setObjectName("secondary")
        self.open_center.clicked.connect(lambda: self.destinationRequested.emit("overview"))
        box.addWidget(self.open_center)
        self._entry = QPropertyAnimation(self, b"windowOpacity", self)
        self._entry.setDuration(130)
        self._entry.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entry.setStartValue(0.65)
        self._entry.setEndValue(1.0)

    def _label(self, text, name):
        label = QLabel(text, self)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _rule(self, layout):
        rule = QFrame(self)
        rule.setObjectName("hair")
        layout.addWidget(rule)

    def present(self, anchor, work):
        self.adjustSize()
        self.move(popup_position(anchor, work, self.size()))
        animate = not reduced_motion(self)
        self.setWindowOpacity(0.65 if animate else 1.0)
        self.show()
        focus_target = self.primary if self.primary.isEnabled() else self.open_center
        focus_target.setFocus()
        if animate:
            self._entry.start()

    def hideEvent(self, event):
        self._entry.stop()
        self.setWindowOpacity(1.0)
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)
