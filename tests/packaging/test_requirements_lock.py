"""requirements.lock.txt must stay a faithful hashed mirror of requirements.txt (offline check)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pins(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().rstrip("\\").strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)$", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
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
