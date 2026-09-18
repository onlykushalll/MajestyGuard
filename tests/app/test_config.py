from app.config import RuntimeSettings, load_settings, save_settings


def test_load_settings_uses_safe_defaults_when_file_is_missing(tmp_path):
    assert load_settings(tmp_path / "settings.json") == RuntimeSettings(
        idle_timeout_seconds=60,
        paused=False,
    )


def test_load_settings_rejects_out_of_range_idle_timeout(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"idle_timeout_seconds": 1, "paused": true}', encoding="utf-8")

    assert load_settings(path) == RuntimeSettings(idle_timeout_seconds=60, paused=True)


def test_load_settings_rejects_invalid_pause_value(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"idle_timeout_seconds": 300, "paused": "yes"}', encoding="utf-8")

    assert load_settings(path) == RuntimeSettings(idle_timeout_seconds=300, paused=False)


def test_save_settings_round_trips_valid_values(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(path, RuntimeSettings(idle_timeout_seconds=300, paused=False))

    assert load_settings(path) == RuntimeSettings(idle_timeout_seconds=300, paused=False)
