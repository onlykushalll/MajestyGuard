"""Measure local model stages on synthetic arrays. Never opens a camera."""
from __future__ import annotations

import argparse
import runpy
import statistics
import sys
import time
from pathlib import Path


def measure(label, fn, samples):
    for _ in range(3):
        fn()
    elapsed = []
    for _ in range(samples):
        start = time.perf_counter()
        fn()
        elapsed.append((time.perf_counter() - start) * 1000)
    p95 = statistics.quantiles(elapsed, n=20, method="inclusive")[18]
    print(f"{label}: median {statistics.median(elapsed):.2f} ms, p95 {p95:.2f} ms (n={samples})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="Packaged daemon's _internal directory")
    parser.add_argument("--samples", type=int, default=20, choices=range(5, 101), metavar="5..100")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verify = runpy.run_path(str(root / "packaging" / "verify_model_bundle.py"))["verify"]
    print(f"Verified {verify(args.resources, root / 'models' / 'model_hashes.reference.json')} models")
    sys.path.insert(0, str(root / "daemon"))
    import cv2
    import numpy as np
    import onnxruntime as ort
    from face_engine import FaceEngine, _PureOnnxBuffaloApp

    cv2.setNumThreads(1)
    # The 320px detector input is dynamic; export-time output-shape warnings
    # would otherwise spam the terminal and distort the microbenchmark.
    ort.set_default_logger_severity(3)
    # Only borrow the preprocessing method. No capture, enrollment or daemon hooks.
    from types import SimpleNamespace
    context = SimpleNamespace(_clahe=cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)))
    detector = _PureOnnxBuffaloApp(root=str(args.resources / "models_insightface"))
    detector.prepare()
    if detector._det_model is None or detector._rec_model is None:
        raise RuntimeError("Could not load the pinned detector and embedding models")
    frame = np.full((480, 640, 3), 85, dtype=np.uint8)
    chip = np.full((112, 112, 3), 128, dtype=np.uint8)
    gallery = np.random.default_rng(7).random((60, 512), dtype=np.float32)
    embedding = gallery[0].copy()
    measure("Low-light preprocessing", lambda: FaceEngine._enhance_frame(context, frame), args.samples)
    measure("Detector on no-face frame", lambda: detector.get(frame), args.samples)
    measure("Embedding on synthetic chip", lambda: detector._rec_model.get_feat(chip), args.samples)
    measure("60-template matrix match", lambda: gallery @ embedding, args.samples)
    print("Camera reads, liveness on real faces, and the lock decision are not measured here.")


if __name__ == "__main__":
    main()
