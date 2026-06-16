"""
MiniFASNet preprocessing probe for MajestyGuard v2.

Diagnostic-only: opens the webcam, detects the primary face, and compares
anti-spoof ONNX probabilities across preprocessing variants. It does not write
frames, does not use IPC, and does not lock the machine.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis

sys.path.insert(0, os.path.dirname(__file__))

from diagnostic_common import enhance_frame as _enhance_frame, select_primary_face as _select_primary_face

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
INSIGHTFACE_DIR = Path(__file__).resolve().parent.parent / "models_insightface"
ANTISPOOF = MODELS_DIR / "antispoof_minifasv2.onnx"


@dataclass(frozen=True)
class Variant:
    name: str
    source: str          # raw | enhanced
    crop: str            # square | rect
    scale: float
    border: str          # reflect | constant
    color: str           # rgb | bgr


VARIANTS = [
    Variant("raw_square12_reflect_rgb", "raw", "square", 1.2, "reflect", "rgb"),
    Variant("raw_square15_reflect_rgb", "raw", "square", 1.5, "reflect", "rgb"),
    Variant("raw_square18_reflect_rgb", "raw", "square", 1.8, "reflect", "rgb"),
    Variant("raw_square15_constant_rgb", "raw", "square", 1.5, "constant", "rgb"),
    Variant("raw_square15_reflect_bgr", "raw", "square", 1.5, "reflect", "bgr"),
    Variant("enh_square12_reflect_rgb", "enhanced", "square", 1.2, "reflect", "rgb"),
    Variant("enh_square15_reflect_rgb", "enhanced", "square", 1.5, "reflect", "rgb"),
    Variant("enh_square18_reflect_rgb", "enhanced", "square", 1.8, "reflect", "rgb"),
    Variant("enh_square15_constant_rgb", "enhanced", "square", 1.5, "constant", "rgb"),
    Variant("enh_square15_reflect_bgr", "enhanced", "square", 1.5, "reflect", "bgr"),
    Variant("enh_rect12_rgb", "enhanced", "rect", 1.2, "reflect", "rgb"),
    Variant("enh_rect15_rgb", "enhanced", "rect", 1.5, "reflect", "rgb"),
]


def _crop(frame: np.ndarray, bbox, variant: Variant) -> Optional[np.ndarray]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)

    if variant.crop == "square":
        crop_w = crop_h = int(max(box_w, box_h) * variant.scale)
    else:
        crop_w = int(box_w * variant.scale)
        crop_h = int(box_h * variant.scale)
    if crop_w <= 1 or crop_h <= 1:
        return None

    center_x = (x1 + x2) * 0.5
    center_y = (y1 + y2) * 0.5
    crop_x1 = int(center_x - crop_w * 0.5)
    crop_y1 = int(center_y - crop_h * 0.5)
    crop_x2 = crop_x1 + crop_w
    crop_y2 = crop_y1 + crop_h

    src_x1 = max(0, crop_x1)
    src_y1 = max(0, crop_y1)
    src_x2 = min(w, crop_x2)
    src_y2 = min(h, crop_y2)
    if src_x2 <= src_x1 or src_y2 <= src_y1:
        return None

    roi = frame[src_y1:src_y2, src_x1:src_x2]
    top = max(0, -crop_y1)
    left = max(0, -crop_x1)
    bottom = max(0, crop_y2 - h)
    right = max(0, crop_x2 - w)
    if top or bottom or left or right:
        border = cv2.BORDER_REFLECT_101 if variant.border == "reflect" else cv2.BORDER_CONSTANT
        roi = cv2.copyMakeBorder(roi, top, bottom, left, right, border, value=(0, 0, 0))

    interpolation = cv2.INTER_LANCZOS4 if max(crop_w, crop_h) < 128 else cv2.INTER_AREA
    return cv2.resize(roi, (128, 128), interpolation=interpolation)


def _run_onnx(session: ort.InferenceSession, input_name: str, roi: np.ndarray, variant: Variant) -> tuple[float, float]:
    if variant.color == "rgb":
        img = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    else:
        img = roi
    img = img.astype(np.float32) / 255.0
    blob = np.transpose(img, (2, 0, 1))[np.newaxis]
    logits = session.run(None, {input_name: blob})[0][0]
    exp_logits = np.exp(logits - np.max(logits))
    probs = exp_logits / exp_logits.sum()
    idx0 = float(probs[0])
    idx1 = float(probs[1]) if len(probs) > 1 else float("nan")
    return idx0, idx1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    print(f"InsightFace models: {INSIGHTFACE_DIR}")
    print(f"Anti-spoof model: {ANTISPOOF}")

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(INSIGHTFACE_DIR),
        providers=["CPUExecutionProvider"],
        allowed_modules=["detection", "recognition"],
    )
    app.prepare(ctx_id=0, det_size=(320, 320))

    session = ort.InferenceSession(str(ANTISPOOF), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Could not open camera {args.camera}")
        return 2
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    samples: dict[str, list[float]] = defaultdict(list)
    no_face = 0
    frame_no = 0
    end_at = time.monotonic() + args.seconds

    while time.monotonic() < end_at:
        ret, raw = cap.read()
        if not ret or raw is None:
            continue
        frame_no += 1
        enhanced = _enhance_frame(raw)
        face = _select_primary_face(enhanced, app.get(enhanced))
        if face is None:
            no_face += 1
            continue

        for variant in VARIANTS:
            source = raw if variant.source == "raw" else enhanced
            roi = _crop(source, face.bbox, variant)
            if roi is None:
                continue
            idx0, idx1 = _run_onnx(session, input_name, roi, variant)
            samples[f"{variant.name}:idx0"].append(idx0)
            samples[f"{variant.name}:idx1"].append(idx1)

        if args.preview:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            cv2.rectangle(enhanced, (x1, y1), (x2, y2), (0, 220, 0), 2)
            cv2.imshow("MajestyGuard ONNX preprocessing probe", enhanced)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()

    print()
    print(f"frames={frame_no} no_face_frames={no_face}")
    if not samples:
        return 2

    print()
    print("Variant summary (idx0 is model real probability by contract)")
    ranked = []
    for variant in VARIANTS:
        idx0 = np.array(samples.get(f"{variant.name}:idx0", []), dtype=np.float32)
        idx1 = np.array(samples.get(f"{variant.name}:idx1", []), dtype=np.float32)
        if idx0.size == 0:
            continue
        row = (
            float(np.median(idx0)),
            variant.name,
            float(np.percentile(idx0, 10)),
            float(np.percentile(idx0, 90)),
            float(np.median(idx1)),
            float(np.percentile(idx1, 10)),
            float(np.percentile(idx1, 90)),
        )
        ranked.append(row)

    for med0, name, p10_0, p90_0, med1, p10_1, p90_1 in sorted(ranked, reverse=True):
        print(
            f"  {name:26s} idx0 median={med0:.3f} p10={p10_0:.3f} p90={p90_0:.3f} | "
            f"idx1 median={med1:.3f} p10={p10_1:.3f} p90={p90_1:.3f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
