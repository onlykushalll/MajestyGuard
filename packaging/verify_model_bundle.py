"""Check the frozen neural-network payload against the source-controlled manifest."""
import hashlib
import json
import sys
from pathlib import Path


def verify(root: Path, manifest: Path) -> int:
    prefixes = ("models/", "models_insightface/")
    pinned = json.loads(manifest.read_text(encoding="utf-8"))
    expected = {name: digest for name, digest in pinned.items() if name.startswith(prefixes)}
    if not expected:
        raise ValueError("empty release model manifest")
    count = len(expected)
    expected["models/model_hashes.reference.json"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    files = {path.relative_to(root).as_posix(): path
             for folder in prefixes for path in (root / folder).rglob("*") if path.is_file()}
    for name in files.keys() - expected.keys():
        raise ValueError(f"unexpected model payload: {name}")
    for name, digest in expected.items():
        if name not in files:
            raise ValueError(f"missing model payload: {name}")
        with files[name].open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"model hash mismatch: {name}")
    return count


if __name__ == "__main__":
    model_count = verify(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"Verified {model_count} packaged models and manifest")
