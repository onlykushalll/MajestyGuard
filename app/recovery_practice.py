"""Keyboard rehearsal. No camera, IPC, credentials, hooks, or Windows lock calls."""
from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.tab_confirm import ACTION_LOCK, TabConfirmGate


class RecoveryPractice(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Practice lock recovery")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(360)
        self.gate = TabConfirmGate()
        self.expiry = QTimer(self, singleShot=True)
        self.expiry.timeout.connect(self._expire)
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 18, 22, 18)
        self.note = QLabel("Simulation only. No camera or Windows lock.", self)
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.pad = QPushButton("Focus keyboard practice", self)
        self.pad.setAutoDefault(False)
        self.pad.clicked.connect(self.pad.setFocus)
        self.pad.setAccessibleDescription("Space retries the camera. Two Tab presses simulate Windows fallback. Escape closes practice.")
        self.pad.installEventFilter(self)
        box.addWidget(self.pad)
        row = QHBoxLayout()
        for text, action in (("Try Space", self.retry), ("Try Tab", self.fallback), ("Done", self.accept)):
            button = QPushButton(text, self)
            button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
        box.addLayout(row)
        self.pad.setFocus()

    def eventFilter(self, obj, event):
        if (obj is self.pad and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Tab)):
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                (self.retry if event.key() == Qt.Key.Key_Space else self.fallback)()
            return True
        return super().eventFilter(obj, event)

    def retry(self):
        self.expiry.stop()
        self.gate.reset()
        self.note.setText("Simulated: camera unavailable. Try Tab twice to practice recovery.")

    def fallback(self):
        if self.gate.press(confirmation_required=True) == ACTION_LOCK:
            self.expiry.stop()
            self.note.setText("Practice complete. The real overlay would now ask Windows to lock.")
        else:
            self.note.setText(f"Press Tab again within {self.gate.window_s:g} seconds.")
            self.expiry.start(int(self.gate.window_s * 1000))

    def _expire(self):
        self.gate.reset()
        self.note.setText("Confirmation expired. Nothing happened. Try Tab again.")
