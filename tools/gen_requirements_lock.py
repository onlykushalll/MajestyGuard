"""Regenerate requirements.lock.txt from the hashes PyPI lists for each pin."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?:\s*;\s*\S.*)?")
SHA256 = re.compile(r"[0-9a-f]{64}")
HEADER = (
    "# Hash-pinned lock for requirements.txt (every wheel/sdist PyPI lists for each pinned version).",
    "# Install with:  python -m pip install --require-hashes -r requirements.lock.txt",
    "# Regenerate after changing requirements.txt:  python tools/gen_requirements_lock.py",
    "# Hard rules in requirements.txt (single opencv, protobuf bound, ...) still apply.",
)


def fetch(name, version):
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                result = json.load(response)
            hashes = {item["digests"]["sha256"] for item in result["urls"]}
            if all(isinstance(value, str) and SHA256.fullmatch(value) for value in hashes):
                return sorted(hashes)
        except (OSError, KeyError, TypeError, ValueError):
            pass
    return []


def main(root=ROOT):
    pins = []
    for raw in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN.fullmatch(line)
        if match is None:
            print("UNPARSED", line, file=sys.stderr)
            return 1
        pins.append((line, match.groups()))
    if not pins:
        print("No pinned dependencies found", file=sys.stderr)
        return 1

    with ThreadPoolExecutor(8) as executor:
        results = list(executor.map(lambda pin: fetch(*pin[1]), pins))
    missing = [line for (line, _), hashes in zip(pins, results) if not hashes]
    if missing:
        for line in missing:
            print("MISSING", line, file=sys.stderr)
        return 1

    lines = list(HEADER)
    for (line, _), hashes in zip(pins, results):
        lines.append(f"{line} \\")
        lines.append(" \\\n".join(f"    --hash=sha256:{value}" for value in hashes))

    temporary = None
    try:
        with NamedTemporaryFile(mode="w", dir=root, encoding="utf-8", newline="\n", delete=False) as out:
            temporary = Path(out.name)
            out.write("\n".join(lines) + "\n")
        temporary.replace(root / "requirements.lock.txt")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"{len(pins)} locked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
