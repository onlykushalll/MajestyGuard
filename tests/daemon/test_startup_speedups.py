import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

import import_speedups
import model_prehash

DAEMON = Path(__file__).resolve().parents[2] / "daemon"


def test_insightface_mask_renderer_is_stubbed_once(monkeypatch):
    name = "insightface.app.mask_renderer"
    monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        assert import_speedups.skip_unused_insightface_modules() == [name]
        stand_in = sys.modules[name]
        assert stand_in.__all__ == []
        assert import_speedups.skip_unused_insightface_modules() == []
    finally:
        sys.modules.pop(name, None)


def test_a_real_module_already_imported_is_left_alone(monkeypatch):
    name = "insightface.app.mask_renderer"
    sentinel = object()
    monkeypatch.setitem(sys.modules, name, sentinel)

    assert import_speedups.skip_unused_insightface_modules() == []
    assert sys.modules[name] is sentinel


def test_prehash_digest_matches_hashlib_and_is_consumed(tmp_path):
    model = tmp_path / "pack" / "det.onnx"
    model.parent.mkdir()
    model.write_bytes(b"model-bytes" * 1000)
    expected = hashlib.sha256(b"model-bytes" * 1000).hexdigest()

    assert model_prehash.start(tmp_path) == 1
    assert model_prehash.start(tmp_path) == 0  # already queued
    assert model_prehash.digest_for(str(model)) == expected

    model.write_bytes(b"swapped model")  # a swapped file must never reuse the old digest
    assert model_prehash.digest_for(str(model)) == hashlib.sha256(b"swapped model").hexdigest()


def test_prehash_ignores_non_onnx_and_missing_directories(tmp_path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    assert model_prehash.start(tmp_path, tmp_path / "does-not-exist") == 0


def test_prehash_propagates_read_errors_like_a_direct_read(tmp_path):
    ghost = tmp_path / "ghost.onnx"

    with pytest.raises(OSError):
        model_prehash.digest_for(str(ghost))


def test_rppg_import_does_not_load_scipy():
    code = (
        "import sys; sys.path.insert(0, r'%s'); import rppg_detector; "
        "print('scipy.signal' in sys.modules)" % DAEMON
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)

    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr


def test_rppg_lazy_scipy_resolves_and_preload_is_safe():
    import rppg_detector

    assert callable(rppg_detector.scipy_signal.butter)  # forces the lazy load
    assert rppg_detector.preload_scipy_signal() is False  # already loaded
