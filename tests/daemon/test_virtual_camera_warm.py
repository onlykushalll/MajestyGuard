import threading
import time

import virtual_camera_detector as vcd
from virtual_camera_detector import VirtualCameraDetector


def test_refresh_in_progress_is_joined_not_failed(monkeypatch):
    detector = VirtualCameraDetector()
    started = threading.Event()
    release = threading.Event()

    def slow_detect(_index):
        started.set()
        release.wait(2.0)
        return False

    monkeypatch.setattr(detector, "_detect", slow_detect)
    results = []
    worker = threading.Thread(target=lambda: results.append(detector.is_virtual(0)))
    worker.start()
    assert started.wait(2.0)

    waiter = threading.Thread(target=lambda: results.append(detector.is_virtual(0)))
    waiter.start()
    time.sleep(0.1)
    assert results == []  # the second caller is waiting for the in-flight refresh
    release.set()
    worker.join(3.0)
    waiter.join(3.0)

    assert results == [False, False]


def test_refresh_that_never_finishes_fails_closed():
    detector = VirtualCameraDetector()
    detector._inflight_wait_s = 0.05
    detector._updating.add(0)

    assert detector.is_virtual(0) is True


def test_failed_detection_clears_the_inflight_flag_and_fails_closed(monkeypatch):
    detector = VirtualCameraDetector()

    def boom(_index):
        raise RuntimeError("pnputil missing")

    monkeypatch.setattr(detector, "_detect", boom)

    assert detector.is_virtual(0) is True
    assert 0 not in detector._updating


def test_pnputil_is_enumerated_once_per_detection_window(monkeypatch):
    detector = VirtualCameraDetector()
    calls = []

    def fake_uncached():
        calls.append(1)
        return [("Integrated Camera", "USB\\VID_0C45&PID_6A10")]

    monkeypatch.setattr(detector, "_enumerate_pnputil_cameras_uncached", fake_uncached)

    assert detector._enumerate_pnputil_cameras() == detector._enumerate_pnputil_cameras()
    assert len(calls) == 1


def test_detect_runs_the_slow_mf_probe_concurrently_with_the_name_lookup(monkeypatch):
    detector = VirtualCameraDetector()
    order = []

    def slow_mf(_index):
        order.append("mf-start")
        time.sleep(0.3)
        order.append("mf-end")
        return True

    def quick_name(_index):
        order.append("name")
        return "Integrated Camera"

    monkeypatch.setattr(detector, "_check_mf_hardware_source", slow_mf)
    monkeypatch.setattr(detector, "_get_camera_name", quick_name)
    monkeypatch.setattr(detector, "_check_clsid_blocklist", lambda _n: False)
    monkeypatch.setattr(detector, "_get_hardware_id", lambda _n: "USB\\VID_1")

    assert detector._detect(0) is False
    assert order.index("name") < order.index("mf-end")  # name lookup did not wait for MF


def test_keep_warm_refreshes_a_missing_verdict_and_stops(monkeypatch):
    detector = VirtualCameraDetector()
    refreshed = threading.Event()

    def fake_detect(_index):
        refreshed.set()
        return False

    monkeypatch.setattr(detector, "_detect", fake_detect)

    detector.keep_warm(0, True, interval_s=5.0)
    assert refreshed.wait(2.0)
    assert detector._cache[0][1] is False

    stop = detector._warm_stop
    detector.keep_warm(0, False)
    assert stop.is_set()


def test_keep_warm_is_idempotent_while_running(monkeypatch):
    detector = VirtualCameraDetector()
    monkeypatch.setattr(detector, "_detect", lambda _i: False)

    detector.keep_warm(0, True, interval_s=5.0)
    first = detector._warm_thread
    detector.keep_warm(0, True, interval_s=5.0)

    assert detector._warm_thread is first
    detector.keep_warm(0, False)


def test_module_helper_toggles_the_singleton(monkeypatch):
    seen = []
    monkeypatch.setattr(vcd._detector, "keep_warm", lambda idx, enabled=True, **kw: seen.append((idx, enabled)))

    vcd.keep_virtual_camera_check_warm(2, True)
    vcd.keep_virtual_camera_check_warm(2, False)

    assert seen == [(2, True), (2, False)]
