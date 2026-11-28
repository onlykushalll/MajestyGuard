"""An ordinary settings dialog for inspecting motion without live protection."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout

from app.aperture import Aperture
from app.cc_theme import reduced_motion


class MotionPreview(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle("Protection motion preview")
        self.resize(430, 370)
        self.setProperty("ccTheme", parent.property("ccTheme"))
        self.setProperty("ccReduceMotion", parent.property("ccReduceMotion"))
        self.setStyleSheet(parent.styleSheet())
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        self._scene = QLabel("Shutter closed", self)
        self._scene.setObjectName("rowTitle")
        self._scene.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._scene)
        plate = QFrame(self)
        plate.setObjectName("aperturePlate")
        plate_box = QVBoxLayout(plate)
        plate_box.setContentsMargins(8, 8, 8, 8)
        self.aperture = Aperture(plate)
        self.aperture.setMinimumHeight(200)
        plate_box.addWidget(self.aperture)
        box.addWidget(plate, 1)
        self.aperture.intro_finished.connect(self._running)
        buttons = QGridLayout()
        self._play_startup = QPushButton("Play startup", self)
        self._show_running = QPushButton("Show running", self)
        self._close_shutter = QPushButton("Close shutter", self)
        close = QPushButton("Done", self)
        for index, (button, command) in enumerate((
            (self._play_startup, self._start), (self._show_running, self._running),
            (self._close_shutter, self._paused), (close, self.accept),
        )):
            button.setObjectName("secondary")
            button.clicked.connect(command)
            buttons.addWidget(button, index // 2, index % 2)
        box.addLayout(buttons)
        if reduced_motion(self):
            self._play_startup.setEnabled(False)
            self._play_startup.setToolTip("Startup animation is off while Reduce Motion is enabled.")
        close.setDefault(True)

    def _start(self):
        self._scene.setText("Startup")
        self.aperture.set_protection_art("starting")

    def _running(self):
        self._scene.setText("Running")
        self.aperture.set_protection_art("running", skip_intro=True)

    def _paused(self):
        self._scene.setText("Shutter closed")
        self.aperture.set_protection_art(None)
