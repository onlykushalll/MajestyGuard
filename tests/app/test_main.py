from pathlib import Path

from app.main import default_daemon_executable, default_paths, parse_args


def test_parse_args_supports_background_start_without_camera_flags():
    args = parse_args(["--background"])

    assert args.background is True


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


def test_default_daemon_executable_uses_packaged_sibling_layout(tmp_path):
    daemon = tmp_path / "MajestyGuardDaemon" / "MajestyGuardDaemon.exe"
    daemon.parent.mkdir()
    daemon.write_bytes(b"daemon")

    assert default_daemon_executable(tmp_path) == daemon
