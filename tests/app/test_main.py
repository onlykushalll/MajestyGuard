from pathlib import Path

from app.main import default_paths, parse_args


def test_parse_args_supports_background_start_without_camera_flags():
    args = parse_args(["--background"])

    assert args.background is True


def test_default_paths_are_root_relative_and_user_state_relative(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    root, settings_path = default_paths(Path("C:/installed/MajestyGuard"))

    assert root == Path("C:/installed/MajestyGuard")
    assert settings_path == local_app_data / "MajestyGuard" / "settings.json"
