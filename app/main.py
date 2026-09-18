"""MajestyGuard Control Center application entry point."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from PyQt6.QtWidgets import QApplication

from app.control_center import ControlCenter
from app.runtime import Supervisor


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MajestyGuard Control Center.")
    parser.add_argument(
        "--background",
        action="store_true",
        help="start in the notification area instead of opening the Control Center window",
    )
    return parser.parse_args(argv)


def default_paths(root: Path | None = None) -> tuple[Path, Path]:
    application_root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "C:/tmp"))
    return application_root, local_app_data / "MajestyGuard" / "settings.json"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root, settings_path = default_paths()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("MajestyGuard")
    app.setQuitOnLastWindowClosed(False)

    supervisor = Supervisor(root, Path(sys.executable), settings_path)
    center = ControlCenter(supervisor, settings_path)
    center.show_tray()

    if args.background and center.enrollment_present():
        center.start_protection()
    else:
        center.show_center()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
