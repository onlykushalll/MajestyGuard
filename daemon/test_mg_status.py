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
    assert "Daemon: running" in text
    assert "MG_ENABLE_LOCK: 1" in text
    assert "MG_ENABLE_WHCDF_IPC: 0" in text
    assert "Smart App Control:" in text
