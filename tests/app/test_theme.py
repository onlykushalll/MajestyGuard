from app import theme


def test_presets_unique_and_complete():
    keys = [t.key for t in theme.PRESETS]
    assert keys == ["midnight", "aurora", "ember", "daylight"]
    assert len(set(keys)) == len(keys)
    for t in theme.PRESETS:
        for value in (t.bg_top, t.accent_a, t.accent_b, t.text, t.on_accent):
            assert theme.hex_to_rgb(value)           # valid #RRGGBB


def test_light_theme_flagged_and_dark_ones_not():
    assert [t.dark for t in theme.PRESETS] == [True, True, True, False]


def test_by_key_unknown_is_default():
    assert theme.by_key("zzz") is theme.DEFAULT
    assert theme.by_key("ember") is theme.EMBER


def test_choice_roundtrip_and_corruption(tmp_path):
    path = tmp_path / "sub" / "appearance.json"
    assert theme.load_choice(path) is theme.DEFAULT          # missing file
    assert theme.save_choice(path, theme.AURORA)
    assert theme.load_choice(path) is theme.AURORA
    path.write_text("{not json", encoding="utf-8")
    assert theme.load_choice(path) is theme.DEFAULT
    path.write_text('["list"]', encoding="utf-8")
    assert theme.load_choice(path) is theme.DEFAULT


def test_stylesheet_uses_theme_colours_and_has_no_placeholders():
    for t in theme.PRESETS:
        css = theme.build_style(t)
        assert t.accent_a in css and t.accent_b in css and t.text in css
        assert "HERO" not in css
