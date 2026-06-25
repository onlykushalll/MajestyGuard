from cmd_server import CMDServer, cmd_payload, parse_cmd


def test_parse_cmd_rejects_non_object_json():
    assert parse_cmd("[]") is None
    assert parse_cmd('"verify_requested"') is None


def test_parse_cmd_truncates_source_to_log_safe_length():
    parsed = parse_cmd('{"cmd":"verify_requested","source":"' + ("x" * 200) + '"}')

    assert parsed == ("verify_requested", "x" * 80)


def test_parse_cmd_normalizes_non_string_source():
    assert parse_cmd('{"cmd":"verify_requested","source":42}') == ("verify_requested", "")


def test_parse_cmd_rejects_test_only_crash_command():
    assert parse_cmd('{"cmd":"simulate_crash","source":"attacker"}') is None


def test_cmd_payload_shape_is_minimal():
    payload = cmd_payload("emergency_lock", "overlay")

    assert payload == {"cmd": "emergency_lock", "source": "overlay"}
    assert set(payload) == {"cmd", "source"}


def test_cmd_server_start_is_idempotent_with_existing_live_thread():
    server = CMDServer(lambda *_: None)
    server._thread = type("Thread", (), {"is_alive": lambda self: True})()

    server.start()

    assert server._thread.is_alive()


def test_cmd_server_accepts_only_expected_ui_pid():
    server = CMDServer(lambda *_: None, expected_client_pid=lambda: 4242)

    assert server._is_expected_client(4242) is True
    assert server._is_expected_client(4243) is False


def test_cmd_server_fails_closed_without_owned_ui_pid():
    server = CMDServer(lambda *_: None)

    assert server._is_expected_client(4242) is False
