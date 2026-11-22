"""MajestyGuard Control Center application entry point."""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Sequence

from app.runtime import Supervisor


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MajestyGuard Control Center.")
    parser.add_argument(
        "--background",
        action="store_true",
        help="start in the notification area instead of opening the Control Center window",
    )
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def default_paths(root: Path | None = None) -> tuple[Path, Path]:
    if root is not None:
        application_root = Path(root)
    elif getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        shared_bundle_root = executable_dir.parent
        application_root = (
            shared_bundle_root
            if (shared_bundle_root / "MajestyGuardDaemon").is_dir()
            else executable_dir
        )
    else:
        application_root = Path(__file__).resolve().parents[1]
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "C:/tmp"))
    return application_root, local_app_data / "MajestyGuard" / "settings.json"


def default_daemon_executable(root: Path) -> Path | None:
    """Find the daemon beside a frozen Control Center, if present."""
    candidates = (
        root / "MajestyGuardDaemon.exe",
        root / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        importlib.import_module("PyQt6.QtWidgets")
        importlib.import_module("app.control_center")
        return 0

    from PyQt6.QtWidgets import QApplication

    from app.control_center import ControlCenter

    root, settings_path = default_paths()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("MajestyGuard")
    app.setQuitOnLastWindowClosed(False)

    supervisor = Supervisor(
        root,
        Path(sys.executable),
        settings_path,
        daemon_executable=default_daemon_executable(root),
    )
    center = ControlCenter(supervisor, settings_path)
    center.show_tray()

    if args.background and center.enrollment_present():
        center.start_protection()
    else:
        center.show_center()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
