import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[2] / "packaging" / "verify_model_bundle.py"


def test_missing_modified_and_unexpected_payloads_are_refused(tmp_path):
    spec = importlib.util.spec_from_file_location("mg_verify_model_bundle", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "bundle"
    (root / "models").mkdir(parents=True)
    (root / "models_insightface").mkdir()
    payload = root / "models/a.onnx"
    manifest = tmp_path / "model_hashes.reference.json"
    manifest.write_text(json.dumps({"models/a.onnx": hashlib.sha256(b"weights").hexdigest()}))
    (root / "models" / manifest.name).write_bytes(manifest.read_bytes())
    with pytest.raises(ValueError, match="missing"):
        module.verify(root, manifest)
    payload.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="hash"):
        module.verify(root, manifest)
    payload.write_bytes(b"weights")
    assert module.verify(root, manifest) == 1
    (root / "models_insightface/gallery.npy").write_bytes(b"not distributable")
    with pytest.raises(ValueError, match="unexpected"):
        module.verify(root, manifest)
