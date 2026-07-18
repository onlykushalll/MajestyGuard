from mg_status import collect_status, format_status


def test_status_reports_policy_and_daemon_process():
    status = collect_status(
        env={
            "MG_ENABLE_LOCK": "1",
            "MG_ENABLE_WHCDF_IPC": "0",
            "MG_ENABLE_SERVICE_IPC": "0",
        },
        process_lines=[
            "pythonw.exe 1234 C:\\tmp\\MajestyGuard\\daemon\\main.py",
        ],
    )

    text = format_status(status)

    assert status["daemon_running"] is True
    assert status["daemon_pid"] == 1234
    assert "running" in text
    assert "PID 1234" in text
    assert "MG_ENABLE_LOCK" in text
    assert "SAC:" in text or "Smart App Control:" in text
