"""requirements.lock.txt must stay a faithful hashed mirror of requirements.txt (offline check)."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pins(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().rstrip("\\").strip()
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==(\S+?)(?:\s*;\s*(\S.*))?", line)
        if m:
            key = (m.group(1).lower().replace("_", "-"), m.group(3))
            pins[key] = m.group(2)
    return pins


def test_lock_matches_requirements_and_every_pin_has_hashes():
    reqs = _pins(ROOT / "requirements.txt")
    lock_path = ROOT / "requirements.lock.txt"
    assert lock_path.exists(), "run tools/gen_requirements_lock.py"
    assert _pins(lock_path) == reqs
    text = lock_path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    pinned = [b for b in blocks if re.match(r"[A-Za-z0-9_.\-]+==", b)]
    assert len(pinned) == len(reqs)
    for block in pinned:
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), block.splitlines()[0]


def _generator():
    source = ROOT / "tools" / "gen_requirements_lock.py"
    spec = importlib.util.spec_from_file_location("gen_requirements_lock", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_preserves_environment_markers(tmp_path, monkeypatch):
    generator = _generator()
    pin = 'demo==1.0 ; python_version < "3.12"'
    (tmp_path / "requirements.txt").write_text(pin, encoding="utf-8")
    monkeypatch.setattr(generator, "fetch", lambda *_: ["a" * 64])
    assert generator.main(tmp_path) == 0
    assert pin in (tmp_path / "requirements.lock.txt").read_text(encoding="utf-8")


def test_generator_preserves_previous_lock_on_failed_fetch(tmp_path, monkeypatch):
    generator = _generator()
    (tmp_path / "requirements.txt").write_text("okay==1\nmissing==2\n", encoding="utf-8")
    old = tmp_path / "requirements.lock.txt"
    old.write_bytes(b"existing lock\n")
    monkeypatch.setattr(generator, "fetch", lambda name, _: ["a" * 64] if name == "okay" else [])
    assert generator.main(tmp_path) != 0
    assert old.read_bytes() == b"existing lock\n"
