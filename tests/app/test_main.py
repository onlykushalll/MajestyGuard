from pathlib import Path

import importlib

import pytest

from app.main import default_daemon_executable, default_paths, main, parse_args


def test_parse_args_supports_background_start_without_camera_flags():
    args = parse_args(["--background"])

    assert args.background is True


def test_parse_args_supports_a_packaging_self_test():
    assert parse_args(["--self-test"]).self_test is True


def test_packaging_self_test_imports_widgets_without_starting_gui(monkeypatch):
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))

    assert main(["--self-test"]) == 0
    assert imported == ["PyQt6.QtWidgets", "app.control_center"]


def test_help_exits_cleanly_when_windowed_stdout_is_unavailable(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0


def test_default_paths_are_root_relative_and_user_state_relative(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    root, settings_path = default_paths(Path("C:/installed/MajestyGuard"))

    assert root == Path("C:/installed/MajestyGuard")
    assert settings_path == local_app_data / "MajestyGuard" / "settings.json"


def test_default_paths_use_the_frozen_executable_directory(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(tmp_path / "MajestyGuard.exe"))

    root, _settings_path = main.default_paths()

    assert root == tmp_path


def test_default_paths_use_shared_bundle_root_for_onedir_layout(monkeypatch, tmp_path):
    from app import main

    bundle_root = tmp_path / "bundle"
    executable_dir = bundle_root / "MajestyGuard"
    executable_dir.mkdir(parents=True)
    (bundle_root / "MajestyGuardDaemon").mkdir()
    executable = executable_dir / "MajestyGuard.exe"

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(executable))

    root, _settings_path = main.default_paths()

    assert root == bundle_root


def test_default_daemon_executable_uses_packaged_sibling_layout(tmp_path):
    daemon = tmp_path / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe"
    daemon.parent.mkdir()
    daemon.write_bytes(b"daemon")

    assert default_daemon_executable(tmp_path) == daemon
