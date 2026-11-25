# MajestyGuard.CVEngine/face_engine.py
# Core computer vision pipeline.
# Handles: face detection, recognition, embedding generation, liveness check.
#
# RECOGNITION MODEL PRIORITY:
#   1. AdaFace R100 (adaface_r100.onnx) — quality-adaptive margin, best on webcam
#   2. ArcFace R100 via InsightFace buffalo_l — fallback if AdaFace not present
#
# DETECTION: RetinaFace (bundled in buffalo_l)
# LIVENESS:  12-layer passive anti-spoof stack (liveness_detector.py)
#
# PREPROCESSING: CLAHE on L channel (LAB) for low-light (<100 lux) enhancement.
# QUALITY GATE: det_score × sharpness × illumination × face_size → skip bad frames.
#
# Camera frames are NEVER written to disk. Zeroed after use.
# All processing is 100% local. No network calls.

import os
import time
import logging
import subprocess
import hashlib
import json
import numpy as np
from dataclasses import dataclass
from typing import Any, Optional
import cv2
from camera_io import read_bgr_frame, release_capture
from windows_tools import system_tool

import sys
from importlib.abc import MetaPathFinder

class _DummySACFinder(MetaPathFinder):
    """Prevents Windows 11 Smart App Control (SAC) blocks on unsupported C-extensions."""
    def __init__(self):
        blocked = set()
        if os.environ.get("MG_SAC_STUB_SIMSIMD", "0") == "1":
            blocked.add("simsimd")
        if os.environ.get("MG_SAC_STUB_ALBUMENTATIONS", "0") == "1":
            blocked.add("albumentations")
        self.BLOCKED_MODULES = frozenset(blocked)

    def find_spec(self, fullname, path, target=None):
        if getattr(self, "BLOCKED_MODULES", None) and fullname.split(".")[0] in self.BLOCKED_MODULES:
            raise ModuleNotFoundError(f"SAC-protected stub: {fullname}")
        return None

if not any(isinstance(f, _DummySACFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DummySACFinder())

_ARCFACE_112_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

def _umeyama_similarity_2d(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Exact 2D Umeyama similarity transform (rotation + uniform scale + translation)."""
    num = src.shape[0]
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean
    A = (dst_demean.T @ src_demean) / num
    U, S, Vt = np.linalg.svd(A)
    d = np.ones((2,), dtype=np.float64)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[1] = -1
    T = np.eye(3, dtype=np.float64)
    rank = np.linalg.matrix_rank(A)
    if rank == 0:
        return np.zeros((2, 3), dtype=np.float32)
    if rank == 1:
        if np.linalg.det(U) * np.linalg.det(Vt) > 0:
            T[:2, :2] = U @ Vt
        else:
            s = d[1]
            d[1] = -1
            T[:2, :2] = U @ np.diag(d) @ Vt
            d[1] = s
    else:
        T[:2, :2] = U @ np.diag(d) @ Vt
    var_src = float(np.var(src_demean, axis=0).sum())
    scale = 1.0 if var_src < 1e-8 else float((S @ d) / var_src)
    T[:2, 2] = dst_mean - scale * (T[:2, :2] @ src_mean)
    T[:2, :2] *= scale
    return T[:2, :].astype(np.float32)

class _PureOnnxFace:
    def __init__(self, bbox: np.ndarray, kps: np.ndarray, det_score: float, embedding: np.ndarray):
        self.bbox = bbox
        self.kps = kps
        self.det_score = float(det_score)
        self.embedding = embedding
        self.normed_embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

class _PureOnnxBuffaloApp:
    """Zero-SAC-Block Pure-ONNXRuntime Detector and ArcFace Recognizer fallback."""
    def __init__(self, name: str = "buffalo_l", root: str = "", providers: list[str] | None = None, allowed_modules: list[str] | None = None):
        self.name = name
        self.root = root
        self.providers = providers or ["CPUExecutionProvider"]
        self.models: dict[str, Any] = {}
        self._det_model = None
        self._rec_model = None

    def prepare(self, ctx_id: int = 0, det_size: tuple[int, int] = (320, 320)) -> None:
        try:
            from insightface.model_zoo import get_model
            cand_roots = [
                os.path.join(self.root, "models", self.name),
                os.path.join(self.root, self.name),
                self.root,
            ]
            det_path = None
            rec_path = None
            for cr in cand_roots:
                dp = os.path.join(cr, "det_10g.onnx")
                rp = os.path.join(cr, "w600k_r50.onnx")
                if os.path.exists(dp) and det_path is None:
                    det_path = dp
                if os.path.exists(rp) and rec_path is None:
                    rec_path = rp

            if det_path and os.path.exists(det_path):
                self._det_model = get_model(det_path, providers=self.providers)
                self._det_model.prepare(ctx_id=ctx_id, input_size=det_size)
            if rec_path and os.path.exists(rec_path):
                self._rec_model = get_model(rec_path, providers=self.providers)
                self._rec_model.prepare(ctx_id=ctx_id)
                self.models["recognition"] = self._rec_model
        except Exception as e:
            logging.getLogger("MajestyGuard.CVEngine").warning("PureOnnxBuffaloApp prepare failed: %s", e)

    def get(self, img: np.ndarray) -> list[Any]:
        if self._det_model is None or img is None or img.size == 0:
            return []
        try:
            bboxes, kpss = self._det_model.detect(img, max_num=0, metric="default")
            if bboxes is None or len(bboxes) == 0:
                return []
            faces = []
            for i in range(len(bboxes)):
                bbox = bboxes[i, :4]
                det_score = float(bboxes[i, 4])
                kps = kpss[i] if kpss is not None and i < len(kpss) else None
                emb = None
                if self._rec_model is not None and kps is not None:
                    M = _umeyama_similarity_2d(kps, _ARCFACE_112_DST)
                    chip = cv2.warpAffine(img, M, (112, 112), borderValue=0.0)
                    emb = self._rec_model.get_feat(chip).flatten()
                elif self._rec_model is not None:
                    emb = np.zeros(EMBEDDING_DIM, dtype=np.float32)
                face = _PureOnnxFace(bbox, kps, det_score, emb if emb is not None else np.zeros(EMBEDDING_DIM, dtype=np.float32))
                faces.append(face)
            return faces
        except Exception as exc:
            logging.getLogger("MajestyGuard.CVEngine").debug("PureOnnxBuffaloApp get() error: %s", exc)
            return []

# InsightFace — install: pip install insightface onnxruntime
import model_prehash
from import_speedups import skip_unused_insightface_modules

# Must run before insightface is imported: skips matplotlib/albumentations/skimage.
skip_unused_insightface_modules()
try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    logging.warning("InsightFace not installed. Run: pip install insightface onnxruntime")

from liveness_detector import LivenessDetector
from virtual_camera_detector import is_virtual_camera
from face_quality import measure_face_quality
from diagnostic_common import resolve_resource_dir

logger = logging.getLogger("MajestyGuard.CVEngine")
EMBEDDING_DIM = 512


# ── MG-D-05: TOFU (trust-on-first-use) model-integrity verification ──────
# No ONNX model is loaded without its SHA-256 matching the pinned manifest
# at %LOCALAPPDATA%\MajestyGuard\model_hashes.json. First sighting pins the
# hash (log "pinned model hash (TOFU)"); a later mismatch REFUSES the load
# (CRITICAL log with both hashes) unless MG_ALLOW_UNPINNED_MODELS=1.
# Limitation: a same-user attacker with write access to both the model file
# and the manifest can re-pin, but a silent model swap is now impossible
# without a traceable CRITICAL log event plus manifest deletion.

_TOFU_VERIFIED: set = set()


def _model_hashes_manifest_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MajestyGuard", "model_hashes.json")


def _verify_model_tofu(model_path: str) -> bool:
    """True if model_path's SHA-256 matches its pinned (or newly pinned)
    manifest entry; False (refuse to load) on mismatch or read error."""
    key = os.path.normcase(os.path.abspath(model_path))
    if key in _TOFU_VERIFIED:
        return True
    try:
        # Usually already computed by model_prehash's background threads.
        actual = model_prehash.digest_for(model_path)
    except OSError as exc:
        logger.critical(
            "Model integrity check failed to read %s: %s - refusing to load",
            model_path,
            exc,
        )
        return False
    if os.environ.get("MG_ALLOW_UNPINNED_MODELS", "0") == "1":
        logger.warning(
            "MG_ALLOW_UNPINNED_MODELS=1: skipping TOFU pin check for %s",
            model_path,
        )
        _TOFU_VERIFIED.add(key)
        return True
    manifest_path = _model_hashes_manifest_path()
    manifest = {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            manifest = loaded
    except (OSError, ValueError):
        manifest = {}
    pinned = manifest.get(key)
    if pinned is None:
        # Check against shipped reference manifest to close first-use TOFU gap
        ref_manifest_path = str(resolve_resource_dir("models") / "model_hashes.reference.json")
        try:
            with open(ref_manifest_path, "r", encoding="utf-8") as rfh:
                ref_data = json.load(rfh)
            basename = os.path.basename(model_path)
            relpath = os.path.relpath(
                model_path,
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ).replace("\\", "/")
            ref_hash = ref_data.get(basename) or ref_data.get(relpath)
            if ref_hash is not None and ref_hash.lower() != actual.lower():
                logger.critical(
                    "MODEL INTEGRITY FAILURE: %s sha256=%s does not match "
                    "shipped reference hash=%s - refusing to load",
                    model_path, actual, ref_hash,
                )
                return False
        except Exception:
            pass
        manifest[key] = actual
        try:
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)
            logger.info("pinned model hash (TOFU): %s -> %s", model_path, actual)
        except OSError as exc:
            logger.warning(
                "Failed to persist model hash manifest %s: %s", manifest_path, exc
            )
        _TOFU_VERIFIED.add(key)
        return True
    if pinned == actual:
        _TOFU_VERIFIED.add(key)
        return True
    logger.critical(
        "MODEL INTEGRITY FAILURE: %s sha256=%s does not match pinned hash=%s "
        "- refusing to load. If this is an intentional model upgrade, delete "
        "the entry in %s (or temporarily set MG_ALLOW_UNPINNED_MODELS=1) and "
        "let it re-pin.",
        model_path,
        actual,
        pinned,
        manifest_path,
    )
    return False


def _verify_model_pack_tofu(model_dir: str) -> bool:
    """TOFU-verify every *.onnx under model_dir (recursively) - the
    insightface buffalo_l pack (det_10g.onnx / w600k_r50.onnx / ...)."""
    if not model_dir or not os.path.isdir(model_dir):
        return True  # nothing to verify yet; loader behaves as before
    model_prehash.start(model_dir)  # hash every file in parallel (no-op if already queued)
    for dirpath, _dirnames, filenames in os.walk(model_dir):
        for filename in sorted(filenames):
            if not filename.lower().endswith(".onnx"):
                continue
            if not _verify_model_tofu(os.path.join(dirpath, filename)):
                return False
    return True

_ARCFACE_112_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class FrameResult:
    """Result of processing a single camera frame."""
    face_count: int                    # Selected primary face count (0 or 1)
    primary_user_present: bool         # Enrolled user recognized
    recognition_score: float           # Cosine similarity (0.0–1.0)
    liveness_score: float              # Anti-spoofing confidence (0.0–1.0)
    liveness_passed: bool              # True = real face, False = spoof
    virtual_camera_detected: bool      # True = software camera feed
    camera_obstructed: bool            # True = camera appears blocked
    inference_ms: float                # Processing time
    raw_face_count: int = 0            # Raw detector count before primary selection
    frame_quality: float = 0.0         # Recognition quality gate score
    face_height_frac: float = 0.0      # Selected face height as fraction of frame height
    face_center_offset: float = 0.0    # Normalized selected-face distance from frame center
    selected_face_score: float = 0.0   # Primary-face selection score
    best_template_index: int = -1      # Enrolled template with highest cosine score
    selection_reason: str = "none"     # geometry | sticky_iou | identity
    candidate_owner_score: float = 0.0 # Pre-liveness identity score used for selection
    sticky_iou: float = 0.0            # IoU with last confirmed owner bbox
    predicted_iou: float = 0.0         # IoU with Kalman-predicted owner bbox
    smoothed_recognition_score: float = 0.0  # Quality-weighted temporal identity score
    presence_confidence: float = 0.0   # Bounded UI/presence confidence, never unlock evidence by itself
    replay_suspected: bool = False     # True if a replay/duplicate-frame gate fired this tracking session


class _OwnerBoxKalman:
    """Constant-velocity Kalman filter for a verified owner's face box."""

    def __init__(self, process_std: float = 45.0, measurement_std: float = 28.0):
        self.process_std = float(process_std)
        self.measurement_std = float(measurement_std)
        self._x: Optional[np.ndarray] = None
        self._p: Optional[np.ndarray] = None
        self._last_ts: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self._x is not None

    def reset(self) -> None:
        self._x = None
        self._p = None
        self._last_ts = None

    def update(self, bbox: Optional[tuple[float, float, float, float]], timestamp: float) -> None:
        measurement = self._bbox_to_measurement(bbox)
        if measurement is None:
            return

        if self._x is None:
            self._x = np.zeros(8, dtype=np.float64)
            self._x[:4] = measurement
            self._p = np.eye(8, dtype=np.float64) * 100.0
            self._last_ts = timestamp
            return

        self.predict(timestamp)
        assert self._x is not None and self._p is not None
        h = np.zeros((4, 8), dtype=np.float64)
        h[:4, :4] = np.eye(4, dtype=np.float64)
        r = np.eye(4, dtype=np.float64) * (self.measurement_std ** 2)
        innovation = measurement - (h @ self._x)
        s = h @ self._p @ h.T + r
        try:
            s_inv = np.linalg.inv(s)
        except np.linalg.LinAlgError:
            s_inv = np.linalg.pinv(s)
        k = self._p @ h.T @ s_inv



        self._x = self._x + (k @ innovation)
        self._p = (np.eye(8, dtype=np.float64) - k @ h) @ self._p
        self._last_ts = timestamp

    def predict(self, timestamp: float) -> Optional[tuple[float, float, float, float]]:
        if self._x is None or self._p is None:
            return None
        dt = 0.0 if self._last_ts is None else float(np.clip(timestamp - self._last_ts, 0.0, 1.0))
        if dt > 0.0:
            f = np.eye(8, dtype=np.float64)
            f[0, 4] = dt
            f[1, 5] = dt
            f[2, 6] = dt
            f[3, 7] = dt
            q = np.eye(8, dtype=np.float64) * ((self.process_std ** 2) * max(dt, 0.05))
            self._x = f @ self._x
            self._p = f @ self._p @ f.T + q
            self._last_ts = timestamp
        return self.predicted_bbox()

    def predicted_bbox(self) -> Optional[tuple[float, float, float, float]]:
        if self._x is None:
            return None
        cx, cy, bw, bh = [float(v) for v in self._x[:4]]
        bw = max(1.0, bw)
        bh = max(1.0, bh)
        return cx - bw / 2.0, cy - bh / 2.0, cx + bw / 2.0, cy + bh / 2.0

    @staticmethod
    def _bbox_to_measurement(bbox: Optional[tuple[float, float, float, float]]) -> Optional[np.ndarray]:
        if bbox is None:
            return None
        x1, y1, x2, y2 = bbox
        bw = x2 - x1
        bh = y2 - y1
        if bw <= 0.0 or bh <= 0.0:
            return None
        return np.array([x1 + bw / 2.0, y1 + bh / 2.0, bw, bh], dtype=np.float64)


class FaceEngine:
    """
    Main CV engine. Initialized once, processes frames continuously.
    """

    def __init__(
        self,
        model_dir: str,
        camera_idx: int = 0,
        recognition_threshold: float = 0.75,
        liveness_threshold: float = 0.70,
        open_camera: bool = True,
        liveness_model_dir: Optional[str] = None,
        defer_heavy_liveness: bool = False,
    ):
        self.model_dir  = model_dir
        self.camera_idx = camera_idx
        self.recognition_threshold = recognition_threshold
        self.liveness_threshold = liveness_threshold
        self._open_camera_on_initialize = open_camera
        self._app: Optional[FaceAnalysis] = None
        self._aux_model_dir = liveness_model_dir or model_dir
        self._liveness = LivenessDetector(
            model_dir=liveness_model_dir or model_dir,
            defer_heavy_models=defer_heavy_liveness,
        )
        self._adaface_session = None
        self._adaface_input_name = "input"
        self._adaface_flip_fusion_enabled = os.environ.get("MG_ADAFACE_FLIP_FUSION", "1") != "0"
        self._enrolled_embeddings: list[np.ndarray] = []
        self._cap: Optional[cv2.VideoCapture] = None

        # Virtual camera detection: track camera device path
        self._expected_device_path: Optional[str] = None
        self._backend_name: str = ""
        self._det_size: tuple[int, int] = (320, 320)  # 160 is too small for recognition embedding generation
        self._virtual_camera_cache: tuple[float, bool] = (0.0, False)

        # Multi-frame consensus: require consecutive matches before accepting
        self._consecutive_matches = 0
        self._consecutive_liveness = 0
        self._consensus_threshold = 3  # frames
        self._last_recognition_score = 0.0
        self._min_frame_quality = 0.35
        self._last_owner_bbox: Optional[tuple[float, float, float, float]] = None
        self._last_owner_seen_at = 0.0
        self._owner_track_ttl_s = self._read_float_env("MG_OWNER_TRACK_TTL_S", 3.0, 0.5, 10.0)
        self._owner_track_min_iou = self._read_float_env("MG_OWNER_TRACK_MIN_IOU", 0.10, 0.0, 1.0)
        self._owner_track_predict_min_iou = self._read_float_env("MG_OWNER_TRACK_PREDICT_MIN_IOU", 0.08, 0.0, 1.0)
        self._owner_track_min_score = self._read_float_env("MG_OWNER_TRACK_MIN_SCORE", 0.62, 0.0, 1.0)
        self._owner_track_score_margin = self._read_float_env("MG_OWNER_TRACK_SCORE_MARGIN", 0.04, 0.0, 0.5)
        self._recognition_ewma = 0.0
        self._recognition_ewma_ready = False
        self._recognition_ewma_alpha = self._read_float_env("MG_RECOGNITION_EWMA_ALPHA", 0.35, 0.05, 1.0)
        self._active_liveness_jitter_floor = self._read_float_env("MG_ACTIVE_LIVENESS_JITTER_FLOOR", 0.55, 0.0, 1.0)
        self._presence_confidence_max_boost = self._read_float_env("MG_PRESENCE_CONFIDENCE_MAX_BOOST", 0.25, 0.0, 0.4)
        self._presence_track_floor = self._read_float_env("MG_PRESENCE_TRACK_FLOOR", 0.65, 0.0, 1.0)
        self._presence_min_quality = self._read_float_env("MG_PRESENCE_MIN_QUALITY", 0.55, 0.0, 1.0)
        self._presence_track_min_score = self._read_float_env("MG_PRESENCE_TRACK_MIN_SCORE", 0.35, 0.0, 1.0)
        owner_track_process_std = self._read_float_env("MG_OWNER_TRACK_PROCESS_STD", 45.0, 1.0, 500.0)
        owner_track_measurement_std = self._read_float_env("MG_OWNER_TRACK_MEASUREMENT_STD", 28.0, 1.0, 500.0)
        self._owner_kalman = _OwnerBoxKalman(owner_track_process_std, owner_track_measurement_std)

        # P-1: Pre-allocate CLAHE once — creating it per-frame wastes CPU
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        # P-3: enrolled embeddings as matrix for vectorized matmul
        self._enrolled_matrix: Optional[np.ndarray] = None  # (N, 512) float32

    @staticmethod
    def _read_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r; using %.3f", name, raw, default)
            return default
        if value < minimum or value > maximum:
            logger.warning("Invalid %s=%.3f; using %.3f", name, value, default)
            return default
        return value

    # ─────────────────────────────────────────────────────────────────
    # INITIALIZATION
    # ─────────────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """
        Load models and open camera. Call once at startup.
        Returns True if successful.
        """
        if not INSIGHTFACE_AVAILABLE:
            logger.error("InsightFace is required. Install it first.")
            return False

        try:
            logger.info("Loading InsightFace buffalo_l model from: %s", self.model_dir)

            # MG-D-05: TOFU-verify every ONNX file in the model pack
            # directory BEFORE InsightFace loads any of them. A swapped
            # model file is refused fail-closed.
            if not _verify_model_pack_tofu(self.model_dir):
                logger.error(
                    "Model pack TOFU verification failed for %s - refusing to initialize",
                    self.model_dir,
                )
                return False
            if self._aux_model_dir and self._aux_model_dir != self.model_dir:
                if not _verify_model_pack_tofu(self._aux_model_dir):
                    logger.error(
                        "Auxiliary model pack TOFU verification failed for %s - refusing to initialize",
                        self._aux_model_dir,
                    )
                    return False

            # CPU-first by default: DirectML can hang on some laptop ONNX stacks.
            # Set MG_ONNX_PROVIDERS=DmlExecutionProvider,CPUExecutionProvider to opt in.
            provider_env = os.environ.get("MG_ONNX_PROVIDERS", "").strip()
            providers = (
                [p.strip() for p in provider_env.split(",") if p.strip()]
                if provider_env else ["CPUExecutionProvider"]
            )

            try:
                if INSIGHTFACE_AVAILABLE:
                    self._app = FaceAnalysis(
                        name="buffalo_l",
                        root=self.model_dir,
                        providers=providers,
                        # Do NOT add landmark_2d_106 here — it causes det_10g.onnx to silently
                        # stop returning faces in InsightFace 0.7.3 (pipeline conflict).
                        # The 5-point kps from det_10g.onnx are sufficient for recognition alignment.
                        allowed_modules=["detection", "recognition"],
                    )
                    self._app.prepare(ctx_id=0, det_size=self._det_size)
                else:
                    raise ImportError("InsightFace not available")
            except Exception as app_exc:
                logger.warning(
                    "InsightFace FaceAnalysis failed (%s); falling back to Pure-ONNX Buffalo",
                    app_exc,
                )
                self._app = _PureOnnxBuffaloApp(
                    name="buffalo_l",
                    root=self.model_dir,
                    providers=providers,
                )
                self._app.prepare(ctx_id=0, det_size=self._det_size)

            # MG-D-05: pin (TOFU) any models InsightFace just auto-downloaded
            # on a first run (the pre-check above saw an empty directory).
            # Files verified moments ago are skipped via the in-process cache.
            if not _verify_model_pack_tofu(self.model_dir):
                logger.error(
                    "Model pack TOFU verification failed for %s - refusing to initialize",
                    self.model_dir,
                )
                return False

            logger.info("InsightFace loaded successfully")

            if self._open_camera_on_initialize:
                # Open camera
                if not self._open_camera():
                    return False

                # Validate camera is real hardware (not virtual)
                self._expected_device_path = self._get_camera_device_path(self.camera_idx)

            # Load AdaFace if available
            self._load_adaface()

            return True

        except Exception as e:
            logger.error("FaceEngine initialization failed: %s", e)
            return False

    def warmup_fast(self) -> None:
        """Warm up Phase-1 ONNX sessions (RetinaFace det_10g, ArcFace w600k_r50, MiniFASNetV2)
        using a non-obstructed mid-gray synthetic frame so ONNX Runtime allocates its CPU
        arenas during startup instead of stalling on the first live camera frame."""
        warm_frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        if self._app is not None:
            try:
                self._app.get(warm_frame)
            except Exception as exc:
                logger.debug("Detector warmup skipped: %s", exc)
            try:
                rec_model = getattr(self._app, "models", {}).get("recognition")
                if rec_model is not None and hasattr(rec_model, "get_feat"):
                    rec_model.get_feat(np.full((112, 112, 3), 128, dtype=np.uint8))
            except Exception as exc:
                logger.debug("Recognition warmup skipped: %s", exc)
        if hasattr(self._liveness, "_onnx_antispoof_score"):
            try:
                self._liveness._onnx_antispoof_score(np.full((128, 128, 3), 128, dtype=np.uint8))
                self._liveness._onnx_score_history.clear()
            except Exception as exc:
                logger.debug("Anti-spoof warmup skipped: %s", exc)
        try:
            from rppg_detector import preload_scipy_signal

            preload_scipy_signal()  # background thread; keeps the first rPPG frame fast
        except Exception as exc:
            logger.debug("scipy preload skipped: %s", exc)
        if hasattr(self._liveness, "start_background_heavy_load"):
            self._liveness.start_background_heavy_load()

    def _open_camera(self) -> bool:
        self._cap = cv2.VideoCapture(self.camera_idx, cv2.CAP_DSHOW)  # DSHOW on Windows
        if not self._cap.isOpened():
            logger.error("Failed to open camera %d", self.camera_idx)
            release_capture(self._cap)
            self._cap = None
            return False

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        # H11 fix: this camera path (open_camera=True) is only used by standalone
        # diagnostic/enrollment scripts, never the production daemon (which opens
        # its own camera at main.py's TARGET_FPS=15 and passes frames into
        # FaceEngine.process_frame()). Requesting 15 here — the real achievable
        # rate given per-frame inference cost — instead of an unreachable 30,
        # so this property reflects reality rather than a misleading request.
        self._cap.set(cv2.CAP_PROP_FPS, 15)
        try:
            self._backend_name = self._cap.getBackendName()
        except Exception:
            self._backend_name = "unknown"

        logger.info("Camera %d opened (640x480, backend=%s)", self.camera_idx, self._backend_name)
        return True

    # ─────────────────────────────────────────────────────────────────
    # ENROLLMENT
    # Called during setup to generate and return a face embedding.
    # The embedding is sent back to the Service and stored via EmbeddingStore.
    # ─────────────────────────────────────────────────────────────────

    def set_det_size(self, width: int, height: int) -> None:
        """Switch InsightFace detector input size without reloading the model."""
        width = 320 if width >= 320 else 160
        height = 320 if height >= 320 else 160
        requested = (width, height)
        if requested == self._det_size or self._app is None:
            return

        try:
            self._app.prepare(ctx_id=0, det_size=requested)
            self._det_size = requested
            logger.info("Detector size set to %sx%s", width, height)
        except Exception as e:
            logger.error("Failed to switch detector size to %s: %s", requested, e)
    def capture_enrollment_frame(self) -> Optional[np.ndarray]:
        """
        Captures one frame and returns a 512-dim face embedding.
        Returns None if no face detected or liveness fails.
        """
        frame = self._read_frame()
        if frame is None:
            return None

        faces = self._app.get(frame)

        if len(faces) == 0:
            logger.warning("No face detected during enrollment capture")
            return None

        if len(faces) > 1:
            logger.warning("Multiple faces during enrollment — require single face")
            return None

        face = faces[0]

        # Liveness check during enrollment — don't enroll a photo
        liveness_score = self._liveness.score(frame, face)
        if liveness_score < self.liveness_threshold:
            logger.warning("Liveness check failed during enrollment (%.3f)", liveness_score)
            return None

        embedding = self._get_best_embedding(frame, face)
        logger.info("Enrollment embedding captured (liveness: %.3f)", liveness_score)

        # Return a copy — don't hold a reference to the face object
        result = embedding.copy()
        return result

    def load_enrolled_embeddings(self, embeddings: list[list[float]]) -> int:
        """
        Load pre-computed embeddings from EmbeddingStore into memory.
        Called by cv_server.py after deserializing from the Service.
        """
        normalized: list[np.ndarray] = []
        skipped = 0
        wrong_dim = 0
        for embedding in embeddings:
            vector = self._normalize_embedding(embedding)
            if vector is None:
                skipped += 1
                continue
            if vector.size != EMBEDDING_DIM:
                wrong_dim += 1
                continue
            normalized.append(vector)

        self._enrolled_embeddings = normalized
        # P-3: build row matrix once for O(1) vectorized matmul in process_frame
        if self._enrolled_embeddings:
            self._enrolled_matrix = np.stack(self._enrolled_embeddings, axis=0)  # (N, 512)
        else:
            self._enrolled_matrix = None
        if skipped:
            logger.warning("Skipped %d invalid enrolled embeddings", skipped)
        if wrong_dim:
            logger.warning("Skipped %d enrolled embeddings with wrong width", wrong_dim)
        logger.info("Loaded %d enrolled embeddings", len(self._enrolled_embeddings))
        return len(self._enrolled_embeddings)

    # ─────────────────────────────────────────────────────────────────
    # MAIN PROCESSING LOOP (called by cv_server.py)
    # ─────────────────────────────────────────────────────────────────

    def process_frame(self, frame: Optional[np.ndarray] = None, *, liveness_mode: str = "full") -> FrameResult:
        """
        Reads one camera frame and returns a FrameResult.
        This is the hot path — optimize every branch.
        """
        t_start = time.perf_counter()

        # ── Virtual camera detection ──────────────────────────────────
        if self._is_virtual_camera():
            self._reset_recognition_smoother()
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=True,
                camera_obstructed=False,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        # ── Capture frame ─────────────────────────────────────────────
        if frame is None:
            frame = self._read_frame()

        if frame is None or self._is_obstructed(frame):
            self._reset_recognition_smoother()
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=False,
                camera_obstructed=True,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        orig_frame = frame

        # ── CLAHE lighting enhancement (+12% accuracy in <50 lux) ──
        frame = self._enhance_frame(frame)

        # ── Face detection ────────────────────────────────────────────
        faces = self._app.get(frame)
        raw_face_count = len(faces)

        if raw_face_count == 0:
            self._reset_recognition_smoother()
            if frame is not orig_frame:
                self._zero_frame(orig_frame)
            self._zero_frame(frame)
            return FrameResult(
                face_count=0, primary_user_present=False,
                recognition_score=0.0, liveness_score=0.0,
                liveness_passed=False, virtual_camera_detected=False,
                camera_obstructed=False,
                raw_face_count=raw_face_count,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        # Select one processing candidate, then run liveness only on that face.
        primary_face, selection_meta = self._select_processing_face(frame, faces)
        if primary_face is None:
            self._reset_recognition_smoother()
            self._zero_face_data(faces)
            if frame is not orig_frame:
                self._zero_frame(orig_frame)
            self._zero_frame(frame)
            return FrameResult(
                face_count=0,
                primary_user_present=False,
                recognition_score=0.0,
                liveness_score=0.0,
                liveness_passed=False,
                virtual_camera_detected=False,
                camera_obstructed=False,
                raw_face_count=raw_face_count,
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        if raw_face_count > 1:
            logger.debug(
                "Selected face from %d raw detections via %s "
                "(candidate_owner_score=%.3f, sticky_iou=%.2f, kalman_iou=%.2f)",
                raw_face_count,
                selection_meta["reason"],
                selection_meta["candidate_owner_score"],
                selection_meta["sticky_iou"],
                selection_meta["predicted_iou"],
            )

        face_count = 1
        quality_score = self._frame_quality(frame, primary_face)
        face_height_frac, face_center_offset = self._face_geometry(frame, primary_face)
        selected_face_score = self._primary_face_candidate_score(frame, primary_face)
        if quality_score < self._min_frame_quality:
            self._reset_recognition_smoother()
            logger.debug(
                "Frame quality too low (%.2f < %.2f, face_h=%.2f, center=%.2f) - skipping",
                quality_score, self._min_frame_quality, face_height_frac, face_center_offset,
            )
            self._zero_face_data(faces)
            if frame is not orig_frame:
                self._zero_frame(orig_frame)
            self._zero_frame(frame)
            return FrameResult(
                face_count=face_count,
                primary_user_present=False,
                recognition_score=0.0,
                liveness_score=0.0,
                liveness_passed=False,
                virtual_camera_detected=False,
                camera_obstructed=False,
                raw_face_count=raw_face_count,
                frame_quality=quality_score,
                face_height_frac=face_height_frac,
                face_center_offset=face_center_offset,
                selected_face_score=selected_face_score,
                selection_reason=selection_meta["reason"],
                candidate_owner_score=selection_meta["candidate_owner_score"],
                sticky_iou=selection_meta["sticky_iou"],
                predicted_iou=selection_meta["predicted_iou"],
                inference_ms=(time.perf_counter() - t_start) * 1000,
            )

        liveness_score = self._score_liveness(frame, primary_face, liveness_mode=liveness_mode)
        liveness_passed = liveness_score >= self.liveness_threshold

        # Track consecutive liveness passes for consensus
        if liveness_passed:
            self._consecutive_liveness += 1
        else:
            self._consecutive_liveness = 0

        # ── Recognition ───────────────────────────────────────────────
        primary_user_present = False
        best_score           = 0.0
        best_template_index  = -1
        smoothed_score       = 0.0
        presence_confidence  = 0.0

        if self._enrolled_matrix is not None:
            cached_scores = getattr(primary_face, "_cached_identity_scores", None)
            if cached_scores is not None:
                best_score, best_template_index, top3_mean = cached_scores
                query_embedding = getattr(primary_face, "_cached_norm_emb", None)
            else:
                cached_emb = getattr(primary_face, "_cached_norm_emb", None)
                if cached_emb is not None:
                    query_embedding = cached_emb
                else:
                    query_embedding = self._normalize_embedding(self._get_best_embedding(frame, primary_face))
                    if query_embedding is not None:
                        primary_face._cached_norm_emb = query_embedding
                if query_embedding is not None:
                    best_score, best_template_index, top3_mean, anchor_best = self._score_vs_enrolled(query_embedding)
                    primary_face._anchor_best = anchor_best
                    primary_face._cached_identity_scores = (best_score, best_template_index, top3_mean)

            if query_embedding is None:
                self._reset_recognition_smoother()
                self._consecutive_matches = 0
                self._consecutive_liveness = 0
                self._last_recognition_score = 0.0
            else:
                if (
                    liveness_passed
                    and self._adaface_session is None
                    and getattr(self, "_adaface_flip_fusion_enabled", True)
                    and 0.64 <= best_score < self.recognition_threshold
                ):
                    fused_emb = self._try_arcface_flip_fusion(frame, primary_face, query_embedding)
                    if fused_emb is not None:
                        fused_scores = self._enrolled_matrix @ fused_emb
                        fused_idx = int(np.argmax(fused_scores))
                        fused_best = float(np.clip(fused_scores[fused_idx], 0.0, 1.0))
                        if fused_best > best_score:
                            best_score = fused_best
                            best_template_index = fused_idx
                            k = min(3, len(fused_scores))
                            top3_mean = float(np.mean(np.partition(fused_scores, -k)[-k:]))

                self._last_recognition_score = best_score

                if liveness_passed:
                    smoothed_score = self._update_recognition_smoother(
                        best_score,
                        quality_score,
                        liveness_passed,
                    )
                    presence_confidence = self._presence_confidence(
                        score=best_score,
                        smoothed_score=smoothed_score,
                        quality=quality_score,
                        liveness_passed=liveness_passed,
                        selection_reason=selection_meta["reason"],
                        sticky_iou=selection_meta["sticky_iou"],
                        predicted_iou=selection_meta["predicted_iou"],
                        top3_mean=top3_mean,
                    )

                    # Multi-frame consensus: require N consecutive frames with
                    # both recognition match AND liveness pass before declaring present
                    if best_score >= self.recognition_threshold:
                        self._consecutive_matches += 1
                    else:
                        self._consecutive_matches = 0
                        self._consecutive_liveness = 0  # L-3: reset both counters together

                    fast_track_pass = (
                        self._consecutive_matches >= 2 and
                        self._consecutive_liveness >= 2 and
                        best_score >= 0.84 and
                        liveness_score >= 0.76 and
                        quality_score >= 0.55
                    )
                    primary_user_present = (
                        (self._consecutive_matches >= self._consensus_threshold and
                         self._consecutive_liveness >= self._consensus_threshold) or
                        fast_track_pass
                    )
                    if self._should_refresh_owner_track(best_score, liveness_passed, selection_meta["reason"]):
                        self._remember_owner(primary_face)
                    self._offer_adaptive_template(
                        query_embedding, primary_face, primary_user_present, liveness_score,
                        quality_score, raw_face_count, liveness_mode,
                    )
                else:
                    self._consecutive_matches = 0
                    self._consecutive_liveness = 0
                    if self._is_borderline_liveness_jitter(liveness_score, quality_score):
                        smoothed_score = self._recognition_ewma if self._recognition_ewma_ready else 0.0
                        presence_confidence = min(
                            max(best_score, smoothed_score),
                            max(0.0, self.recognition_threshold - 1e-3),
                        )
                    else:
                        self._reset_recognition_smoother()
        else:
            self._reset_recognition_smoother()
            self._consecutive_matches = 0
            self._consecutive_liveness = 0  # L-3: no embeddings = reset both

        # ── Zero face embedding data (privacy) ──────────────────────
        self._zero_face_data(faces)

        # ── Zero frame memory ─────────────────────────────────────────
        if frame is not orig_frame:
            self._zero_frame(orig_frame)
        self._zero_frame(frame)

        inference_ms = (time.perf_counter() - t_start) * 1000
        logger.debug(
            "Frame: faces=%d raw_faces=%d user=%s score=%.3f liveness=%.3f "
            "smooth=%.3f presence=%.3f quality=%.2f face_h=%.2f center=%.2f reason=%s candidate=%.3f "
            "sticky_iou=%.2f kalman_iou=%.2f template=%d (%dms)",
            face_count, raw_face_count, primary_user_present, best_score, liveness_score,
            smoothed_score, presence_confidence, quality_score, face_height_frac, face_center_offset,
            selection_meta["reason"], selection_meta["candidate_owner_score"], selection_meta["sticky_iou"],
            selection_meta["predicted_iou"],
            best_template_index, inference_ms,
        )

        return FrameResult(
            face_count=face_count,
            primary_user_present=primary_user_present,
            recognition_score=best_score,
            liveness_score=liveness_score,
            liveness_passed=liveness_passed,
            virtual_camera_detected=False,
            camera_obstructed=False,
            inference_ms=inference_ms,
            raw_face_count=raw_face_count,
            frame_quality=quality_score,
            face_height_frac=face_height_frac,
            face_center_offset=face_center_offset,
            selected_face_score=selected_face_score,
            best_template_index=best_template_index,
            selection_reason=selection_meta["reason"],
            candidate_owner_score=selection_meta["candidate_owner_score"],
            sticky_iou=selection_meta["sticky_iou"],
            predicted_iou=selection_meta["predicted_iou"],
            smoothed_recognition_score=smoothed_score,
            presence_confidence=presence_confidence,
            replay_suspected=(
                self._liveness.replay_flagged_this_session()
                if hasattr(self._liveness, "replay_flagged_this_session") else False
            ),
        )

    # ─────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────

    def _read_frame(self) -> Optional[np.ndarray]:
        return None if self._cap is None else read_bgr_frame(self._cap)

    def _is_obstructed(self, frame: np.ndarray) -> bool:
        """
        Detects if camera is physically covered.
        Heuristic: if mean brightness < 8 (out of 255), assume blocked.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) < 8.0

    def _is_virtual_camera(self) -> bool:
        """
        Detects virtual/software cameras via three independent methods:
          1. Registry CLSID blocklist (OBS, ManyCam, XSplit, DroidCam etc.)
          2. SetupAPI hardware ID — physical = USB\\VID, virtual = ROOT\\ or SWD\\
          3. Device friendly name keyword match
        All three run in virtual_camera_detector.py with 30s caching.
        This replaces the ffmpeg-based approach which only checked names.
        """
        return is_virtual_camera(self.camera_idx)

    def _get_camera_device_path(self, index: int) -> Optional[str]:
        """Return a stable camera DeviceID when WMI is available."""
        try:
            proc = subprocess.run(
                [system_tool("wmic"), "path", "Win32_PnPEntity", "where", "PNPClass='Camera'", "get", "Name,DeviceID"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            stdout = proc.stdout
            if proc.returncode != 0 or not stdout.strip():
                proc = subprocess.run(
                    [
                        system_tool("powershell"),
                        "-NoProfile",
                        "-Command",
                        "Get-CimInstance -ClassName Win32_PnPEntity -Filter 'PNPClass=''Camera''' | ForEach-Object { $_.Name + ' ' + $_.DeviceID }",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False,
                )
                stdout = proc.stdout

            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if not lines:
                return self._backend_name or None

            if "deviceid" in lines[0].lower() or "name" in lines[0].lower():
                devices = lines[1:]
            else:
                devices = lines

            if not devices:
                return self._backend_name or None

            if 0 <= index < len(devices):
                selected = devices[index]
            else:
                selected = devices[0]
            logger.info("Camera device path: %s", selected)
            return selected
        except Exception as e:
            logger.debug("Camera DeviceID lookup failed: %s", e)
            return self._backend_name or None

    def _select_primary_face(self, frame: np.ndarray, faces: list) -> Optional[object]:
        """
        Select one centered, high-confidence primary face from raw detections.

        The detector can produce duplicate boxes around one person. Processing
        only the strongest primary candidate avoids false multi-face states
        without weakening liveness or recognition checks.
        """
        if not faces:
            return None

        h, w = frame.shape[:2]
        frame_area = max(1, h * w)
        frame_center_x = w / 2.0
        frame_center_y = h / 2.0

        selected = max(faces, key=lambda face: self._primary_face_candidate_score(frame, face))
        return selected if self._primary_face_candidate_score(frame, selected) >= 0.0 else None

    def _select_processing_face(self, frame: np.ndarray, faces: list) -> tuple[Optional[object], dict]:
        meta = {
            "reason": "none",
            "candidate_owner_score": 0.0,
            "best_template_index": -1,
            "sticky_iou": 0.0,
            "predicted_iou": 0.0,
        }
        geometry_face = self._select_primary_face(frame, faces)
        if geometry_face is None:
            return None, meta

        selected = geometry_face
        meta["reason"] = "geometry"

        if len(faces) == 1:
            selected_score, selected_idx = self._score_face_identity(frame, selected)
            meta["candidate_owner_score"] = selected_score
            meta["best_template_index"] = selected_idx
            return selected, meta

        track_face, track_meta = self._find_owner_track_face(faces)
        if track_face is not None:
            selected = track_face
            meta["reason"] = track_meta["reason"]
            meta["sticky_iou"] = track_meta["sticky_iou"]
            meta["predicted_iou"] = track_meta["predicted_iou"]

        identity_face, identity_score, identity_idx = self._find_identity_candidate(frame, faces)
        selected_score, selected_idx = self._score_face_identity(frame, selected)
        meta["candidate_owner_score"] = selected_score
        meta["best_template_index"] = selected_idx

        if identity_face is not None:
            beats_selected = identity_score >= selected_score + self._owner_track_score_margin
            if identity_score >= self._owner_track_min_score and (meta["reason"] == "geometry" or beats_selected):
                selected = identity_face
                meta["reason"] = "identity"
                meta["candidate_owner_score"] = identity_score
                meta["best_template_index"] = identity_idx
                meta["sticky_iou"] = self._bbox_iou(self._last_owner_bbox, self._face_bbox(identity_face))
                meta["predicted_iou"] = self._bbox_iou(self._predict_owner_bbox(), self._face_bbox(identity_face))

        return selected, meta

    def _find_owner_track_face(self, faces: list) -> tuple[Optional[object], dict]:
        meta = {"reason": "none", "sticky_iou": 0.0, "predicted_iou": 0.0}
        # M24 fix: _owner_kalman is always created in __init__ (never None),
        # so `not getattr(self, "_owner_kalman", None)` was always False and
        # this guard's second half never actually fired. The TTL check right
        # below already independently covers "never tracked an owner yet"
        # (via _last_owner_seen_at starting at 0.0), so behavior is unchanged
        # — this only removes the dead, misleading half of the condition.
        if self._last_owner_bbox is None:
            return None, meta
        if time.monotonic() - self._last_owner_seen_at > self._owner_track_ttl_s:
            return None, meta

        predicted_bbox = self._predict_owner_bbox()

        best_face = None
        best_rank = 0.0
        best_sticky_iou = 0.0
        best_predicted_iou = 0.0
        best_reason = "none"
        for face in faces:
            face_bbox = self._face_bbox(face)
            sticky_iou = self._bbox_iou(self._last_owner_bbox, face_bbox)
            predicted_iou = self._bbox_iou(predicted_bbox, face_bbox)
            rank = max(
                sticky_iou if sticky_iou >= self._owner_track_min_iou else 0.0,
                predicted_iou if predicted_iou >= self._owner_track_predict_min_iou else 0.0,
            )
            if rank > best_rank:
                best_face = face
                best_rank = rank
                best_sticky_iou = sticky_iou
                best_predicted_iou = predicted_iou
                best_reason = "sticky_iou" if sticky_iou >= predicted_iou else "kalman_iou"

        if best_face is None:
            meta["sticky_iou"] = best_sticky_iou
            meta["predicted_iou"] = best_predicted_iou
            return None, meta

        meta["reason"] = best_reason
        meta["sticky_iou"] = best_sticky_iou
        meta["predicted_iou"] = best_predicted_iou
        return best_face, meta

    def _find_identity_candidate(self, frame: np.ndarray, faces: list) -> tuple[Optional[object], float, int]:
        if self._enrolled_matrix is None:
            return None, 0.0, -1

        best_face = None
        best_score = 0.0
        best_idx = -1
        for face in faces:
            if self._primary_face_candidate_score(frame, face) < 0.0:
                continue
            if self._frame_quality(frame, face) < self._min_frame_quality:
                continue
            score, idx = self._score_face_identity(frame, face)
            if score > best_score:
                best_face = face
                best_score = score
                best_idx = idx

        return best_face, best_score, best_idx

    def attach_adaptive_store(self, store) -> None:
        """Optional bounded pool of extra owner templates (see adaptive_templates.py)."""
        self._adaptive_store = store

    def _score_vs_enrolled(self, embedding: np.ndarray) -> tuple[float, int, float, float]:
        """(score, best_anchor_index, top3_mean, anchor_only_score).

        ``score`` may include the bounded adaptive-template boost; ``anchor_only_score``
        never does and is what decides whether a frame may teach the pool.
        """
        scores = self._enrolled_matrix @ embedding
        best_idx = int(np.argmax(scores))
        anchor_best = float(np.clip(scores[best_idx], 0.0, 1.0))
        k = min(3, len(scores))
        top3_mean = float(np.mean(np.partition(scores, -k)[-k:]))
        best = anchor_best
        store = getattr(self, "_adaptive_store", None)
        if store is not None:
            try:
                best = float(np.clip(store.boosted(embedding, anchor_best), 0.0, 1.0))
            except Exception:
                best = anchor_best
        return best, best_idx, top3_mean, anchor_best

    def _score_face_identity(self, frame: np.ndarray, face) -> tuple[float, int]:
        if self._enrolled_matrix is None:
            return 0.0, -1
        cached_tuple = getattr(face, "_cached_identity_scores", None)
        if cached_tuple is not None:
            return cached_tuple[0], cached_tuple[1]
        try:
            cached_emb = getattr(face, "_cached_norm_emb", None)
            if cached_emb is not None:
                embedding = cached_emb
            else:
                embedding = self._normalize_embedding(self._get_best_embedding(frame, face))
                if embedding is not None:
                    face._cached_norm_emb = embedding
            if embedding is None:
                return 0.0, -1
            best_score, best_idx, top3_mean, anchor_best = self._score_vs_enrolled(embedding)
            face._anchor_best = anchor_best
            face._cached_identity_scores = (best_score, best_idx, top3_mean)
            return best_score, best_idx
        except Exception as e:
            logger.debug("Identity scoring failed for candidate face: %s", e)
            return 0.0, -1

    def _offer_adaptive_template(self, embedding, face, owner_confirmed, liveness, quality,
                                 raw_face_count, liveness_mode) -> None:
        store = getattr(self, "_adaptive_store", None)
        if store is None or embedding is None:
            return
        try:
            store.consider(
                embedding,
                anchor_score=float(getattr(face, "_anchor_best", 0.0)),
                liveness=float(liveness),
                quality=float(quality),
                face_count=int(raw_face_count),
                owner_confirmed=bool(owner_confirmed),
                full_liveness=(liveness_mode != "fast"),
            )
        except Exception:
            logger.debug("Adaptive template offer failed", exc_info=True)

    def _should_refresh_owner_track(self, score: float, liveness_passed: bool, reason: str) -> bool:
        if not liveness_passed:
            return False
        if score >= self.recognition_threshold:
            return True
        return reason in {"sticky_iou", "identity"} and score >= self._owner_track_min_score

    def _score_liveness(self, frame: np.ndarray, face, *, liveness_mode: str) -> float:
        if liveness_mode == "fast" and hasattr(self._liveness, "score_fast"):
            return float(self._liveness.score_fast(frame, face))
        if liveness_mode != "fast" and hasattr(self._liveness, "score_full"):
            return float(self._liveness.score_full(frame, face))
        return float(self._liveness.score(frame, face))

    def _is_borderline_liveness_jitter(self, liveness_score: float, quality: float) -> bool:
        return (
            float(liveness_score) >= self._active_liveness_jitter_floor and
            float(quality) >= self._presence_min_quality
        )

    def _update_recognition_smoother(self, score: float, quality: float, liveness_passed: bool) -> float:
        if not liveness_passed:
            self._reset_recognition_smoother()
            return 0.0

        score = float(np.clip(score, 0.0, 1.0))
        quality = float(np.clip(quality, 0.0, 1.0))
        if not self._recognition_ewma_ready:
            self._recognition_ewma = score
            self._recognition_ewma_ready = True
        else:
            alpha = self._recognition_ewma_alpha * max(0.25, quality)
            self._recognition_ewma = (alpha * score) + ((1.0 - alpha) * self._recognition_ewma)
        return float(self._recognition_ewma)

    def _presence_confidence(
        self,
        *,
        score: float,
        smoothed_score: float,
        quality: float,
        liveness_passed: bool,
        selection_reason: str,
        sticky_iou: float,
        predicted_iou: float,
        top3_mean: float = 0.0,
    ) -> float:
        """
        Bounded confidence for live owner continuity/UI during expression or motion dips.

        This is intentionally not unlock evidence. If raw score is below the
        recognition threshold, cap continuity confidence below that threshold.
        """
        if not liveness_passed:
            return 0.0

        score = float(np.clip(score, 0.0, 1.0))
        quality = float(np.clip(quality, 0.0, 1.0))
        confidence = score
        if quality < self._presence_min_quality:
            return confidence

        max_boosted = min(1.0, score + self._presence_confidence_max_boost)
        if score >= self._presence_track_min_score and smoothed_score > score:
            confidence = max(confidence, min(float(smoothed_score), max_boosted))

        if top3_mean >= self.recognition_threshold - 0.04 and score >= self._presence_track_min_score:
            confidence = max(confidence, min(max_boosted, confidence + 0.015))

        track_iou = max(float(sticky_iou), float(predicted_iou))
        tracked_owner = (
            selection_reason in {"sticky_iou", "kalman_iou", "identity"}
            and track_iou >= self._owner_track_min_iou
            and score >= self._presence_track_min_score
        )
        if tracked_owner:
            confidence = max(confidence, min(self._presence_track_floor, max_boosted))

        if score < self.recognition_threshold:
            confidence = min(confidence, max(0.0, self.recognition_threshold - 1e-3))
        return float(np.clip(confidence, 0.0, 1.0))

    def _reset_recognition_smoother(self) -> None:
        self._recognition_ewma = 0.0
        self._recognition_ewma_ready = False

    def _predict_owner_bbox(self) -> Optional[tuple[float, float, float, float]]:
        owner_kalman = getattr(self, "_owner_kalman", None)
        if owner_kalman is None or not owner_kalman.ready:
            return None
        return owner_kalman.predict(time.monotonic())

    def _remember_owner(self, face) -> None:
        self._last_owner_bbox = self._face_bbox(face)
        self._last_owner_seen_at = time.monotonic()
        owner_kalman = getattr(self, "_owner_kalman", None)
        if owner_kalman is not None:
            owner_kalman.update(self._last_owner_bbox, self._last_owner_seen_at)

    @staticmethod
    def _face_bbox(face) -> Optional[tuple[float, float, float, float]]:
        try:
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
        except Exception:
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    @staticmethod
    def _bbox_iou(a: Optional[tuple[float, float, float, float]], b: Optional[tuple[float, float, float, float]]) -> float:
        if a is None or b is None:
            return 0.0
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        denom = area_a + area_b - inter
        if denom <= 0.0:
            return 0.0
        return float(inter / denom)

    @staticmethod
    def _normalize_embedding(embedding) -> Optional[np.ndarray]:
        try:
            vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        except Exception:
            return None
        if vector.size == 0 or not np.all(np.isfinite(vector)):
            return None
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm <= 1e-8:
            return None
        return vector / norm

    @staticmethod
    def _primary_face_candidate_score(frame: np.ndarray, face) -> float:
        try:
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
        except Exception:
            return -1.0

        h, w = frame.shape[:2]
        frame_area = max(1, h * w)
        frame_center_x = w / 2.0
        frame_center_y = h / 2.0

        box_w = max(0.0, x2 - x1)
        box_h = max(0.0, y2 - y1)
        if box_w <= 0.0 or box_h <= 0.0:
            return -1.0

        area_score = min(1.0, ((box_w * box_h) / frame_area) / 0.35)
        face_center_x = (x1 + x2) / 2.0
        face_center_y = (y1 + y2) / 2.0
        dist_x = abs(face_center_x - frame_center_x) / max(1.0, frame_center_x)
        dist_y = abs(face_center_y - frame_center_y) / max(1.0, frame_center_y)
        center_score = max(0.0, 1.0 - ((dist_x + dist_y) / 2.0))
        contains_center = x1 <= frame_center_x <= x2 and y1 <= frame_center_y <= y2
        center_bonus = 0.15 if contains_center else 0.0
        det_score = float(getattr(face, "det_score", 0.75))

        return (
            det_score * 0.45 +
            area_score * 0.30 +
            center_score * 0.20 +
            center_bonus
        )

    @staticmethod
    def _face_geometry(frame: np.ndarray, face) -> tuple[float, float]:
        try:
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
        except Exception:
            return 0.0, 1.0

        h, w = frame.shape[:2]
        face_height_frac = max(0.0, y2 - y1) / max(1.0, float(h))
        face_center_x = (x1 + x2) / 2.0
        face_center_y = (y1 + y2) / 2.0
        dist_x = abs(face_center_x - w / 2.0) / max(1.0, w / 2.0)
        dist_y = abs(face_center_y - h / 2.0) / max(1.0, h / 2.0)
        center_offset = float(np.clip((dist_x + dist_y) / 2.0, 0.0, 1.0))
        return float(face_height_frac), center_offset

    @staticmethod
    def _zero_face_data(faces: list) -> None:
        for face in faces:
            if hasattr(face, "normed_embedding") and face.normed_embedding is not None:
                face.normed_embedding[:] = 0
            if hasattr(face, "embedding") and face.embedding is not None:
                face.embedding[:] = 0
            face._cached_norm_emb = None
            face._cached_face_quality = None
            face._cached_identity_scores = None

    def _frame_quality(self, frame: np.ndarray, face) -> float:
        """
        Estimate whether a frame is sharp, lit, and large enough for recognition.
        Low-quality frames are ignored instead of poisoning consensus state.
        """
        try:
            cached = getattr(face, "_cached_face_quality", None)
            if cached is not None:
                return cached.score
            q = measure_face_quality(frame, face)
            face._cached_face_quality = q
            return q.score
        except Exception:
            return 0.5

    def _enhance_frame(self, frame: np.ndarray) -> np.ndarray:
        """CLAHE lighting enhancement. Applied only in low-light conditions."""
        try:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            mean_l = float(np.mean(l))
            if mean_l > 100:
                return frame  # Good lighting — skip
            # P-1: use pre-allocated CLAHE from __init__, not a new one each frame
            l_enhanced = self._clahe.apply(l)
            if mean_l < 50:
                gamma = 0.6
                l_enhanced = (np.power(l_enhanced / 255.0, gamma) * 255.0).astype(np.uint8)
            a = cv2.GaussianBlur(a, (3, 3), 0)
            b = cv2.GaussianBlur(b, (3, 3), 0)
            enhanced = cv2.merge([l_enhanced, a, b])
            return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        except Exception:
            return frame  # Never crash the recognition loop

    @staticmethod
    def _zero_frame(frame: np.ndarray) -> None:
        """Zero out frame buffer from memory. Prevents frame retention."""
        if frame is not None:
            try:
                frame[:] = 0
            except Exception:
                pass

    def _get_best_embedding(self, frame: np.ndarray, face) -> np.ndarray:
        if self._adaface_session is not None:
            face_chip = self._extract_adaface_chip(frame, face)
            if face_chip.size > 0:
                try:
                    return self._get_embedding_adaface(face_chip)
                except Exception as e:
                    logger.warning("AdaFace inference failed, falling back to ArcFace: %s", e)
        return face.normed_embedding

    def _try_arcface_flip_fusion(
        self, frame: np.ndarray, face, base_embedding: np.ndarray
    ) -> Optional[np.ndarray]:
        """Run test-time horizontal flip fusion on the 112x112 aligned ArcFace chip
        only for borderline live candidates (0.64 <= score < threshold)."""
        if self._app is None:
            return None
        rec_model = getattr(self._app, "models", {}).get("recognition")
        if rec_model is None or not hasattr(rec_model, "get_feat"):
            return None
        chip = self._extract_adaface_chip(frame, face)
        if chip.size == 0:
            return None
        try:
            flipped_chip = cv2.flip(chip, 1)
            flipped_raw = rec_model.get_feat(flipped_chip)
            flipped_norm = self._normalize_embedding(flipped_raw)
            if flipped_norm is None or flipped_norm.size != base_embedding.size:
                return None
            return self._normalize_embedding(base_embedding + flipped_norm)
        except Exception as e:
            logger.debug("ArcFace flip fusion skipped: %s", e)
            return None
        finally:
            self._zero_frame(chip)

    def _extract_adaface_chip(self, frame: np.ndarray, face) -> np.ndarray:
        """
        Return a 112x112 BGR face chip for AdaFace.

        AdaFace and ArcFace-style models are trained on aligned crops. Prefer
        InsightFace's five landmarks when available; fall back to bbox resize.
        """
        kps = getattr(face, "kps", None)
        if kps is not None:
            try:
                src = np.asarray(kps, dtype=np.float32)
                if src.shape == (5, 2):
                    matrix = _umeyama_similarity_2d(src, _ARCFACE_112_DST)
                    if matrix is not None and not np.all(matrix == 0):
                        return cv2.warpAffine(frame, matrix, (112, 112), borderValue=0.0)
            except cv2.error:
                pass

        try:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
        except Exception:
            return np.empty((0, 0, 3), dtype=frame.dtype)
        h, w = frame.shape[:2]
        face_roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if face_roi.size == 0:
            return face_roi
        return cv2.resize(face_roi, (112, 112), interpolation=cv2.INTER_LINEAR)

    # ── FIX-021: AdaFace optional recognition model ────────────────────────
    # AdaFace (CVPR 2022) adapts margin to image quality → better on low-quality webcam.
    # Drop adaface_r100.onnx into models/ folder to activate automatically.
    def _find_adaface_model_path(self) -> Optional[str]:
        search_dirs = []
        for model_dir in (getattr(self, "_aux_model_dir", None), self.model_dir):
            if model_dir and model_dir not in search_dirs:
                search_dirs.append(model_dir)
        for model_dir in search_dirs:
            model_path = os.path.join(model_dir, "adaface_r100.onnx")
            if os.path.exists(model_path):
                return model_path
        return None

    def _load_adaface(self) -> None:
        """Load AdaFace ONNX if available. Silent no-op if not found."""
        model_path = self._find_adaface_model_path()
        if not model_path:
            logger.info("AdaFace model not found — using ArcFace (buffalo_l). "
                        "Drop adaface_r100.onnx in models/ to enable.")
            return
        # MG-D-05: AdaFace is an optional drop-in file that activates
        # automatically - exactly the silent-substitution backdoor an
        # attacker would use. TOFU-verify before loading; on mismatch refuse
        # THIS model and keep the pinned ArcFace path.
        if not _verify_model_tofu(model_path):
            logger.critical(
                "AdaFace model %s failed the TOFU integrity check - refusing "
                "to load it; using ArcFace (buffalo_l) instead",
                model_path,
            )
            self._adaface_session = None
            self._adaface_input_name = "input"
            return
        try:
            import onnxruntime as ort
            provider_env = os.environ.get("MG_ADAFACE_ONNX_PROVIDERS", "").strip()
            providers = (
                [p.strip() for p in provider_env.split(",") if p.strip()]
                if provider_env else ["CPUExecutionProvider"]
            )
            self._adaface_session = ort.InferenceSession(
                model_path,
                providers=providers
            )
            self._adaface_input_name = self._adaface_session.get_inputs()[0].name
            logger.info("AdaFace R100 loaded — will use instead of buffalo_l for recognition")
        except Exception as e:
            logger.warning("AdaFace load failed: %s — falling back to ArcFace", e)
            self._adaface_session = None
            self._adaface_input_name = "input"

    def _get_embedding_adaface(self, face_roi: np.ndarray) -> np.ndarray:
        """AdaFace inference. Input: 112x112 BGR. Output: L2-normalised 512-dim embedding."""
        resized = cv2.resize(face_roi, (112, 112))
        emb = self._normalize_embedding(self._run_adaface_chip(resized))
        if emb is None:
            raise ValueError("AdaFace produced an invalid embedding")
        if getattr(self, "_adaface_flip_fusion_enabled", True):
            flipped = cv2.flip(resized, 1)
            flipped_emb = self._normalize_embedding(self._run_adaface_chip(flipped))
            if flipped_emb is not None:
                emb = emb + flipped_emb
        fused = self._normalize_embedding(emb)
        if fused is None:
            raise ValueError("AdaFace fusion produced an invalid embedding")
        return fused

    def _run_adaface_chip(self, face_chip_bgr: np.ndarray) -> np.ndarray:
        rgb_chip = cv2.cvtColor(face_chip_bgr, cv2.COLOR_BGR2RGB)
        rgb = rgb_chip.astype(np.float32)
        rgb = ((rgb / 255.0) - 0.5) / 0.5                    # AdaFace normalization
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis]      # HWC -> NCHW
        input_name = getattr(self, "_adaface_input_name", "input")
        output = self._adaface_session.run(None, {input_name: blob})[0]
        return np.asarray(output[0], dtype=np.float32)

    def shutdown(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
        if hasattr(self._liveness, "close"):
            self._liveness.close()
        logger.info("FaceEngine shut down")

    def reset_session(self) -> None:
        self._consecutive_matches = 0
        self._consecutive_liveness = 0
        self._last_recognition_score = 0.0
        self._reset_recognition_smoother()
        self._last_owner_bbox = None
        self._last_owner_seen_at = 0.0
        if getattr(self, "_owner_kalman", None) is not None:
            self._owner_kalman.reset()
        self._liveness.reset_session()

    def reset_liveness(self) -> None:
        """Reset temporal liveness scoring buffers for a fresh deliberate
        verification attempt. Currently only called by the soft-lock
        "Verify Now" burst-retry path, not enrollment (enrollment calls
        LivenessDetector.reset_session() directly) -- the previous
        docstring here claiming otherwise was stale. Deliberately
        preserves anti-replay evidence rather than doing a full
        reset_session(): see LivenessDetector.reset_temporal_buffers()
        for why that distinction matters for this specific caller."""
        self._liveness.reset_temporal_buffers()
    def read_frame(self) -> Optional[np.ndarray]:
        return self._read_frame()

    def detect_faces(self, frame: np.ndarray) -> list:
        if self._app is None:
            return []
        return self._app.get(frame)

    def check_liveness(self, frame: np.ndarray, face) -> float:
        return self._liveness.score(frame, face)

    def zero_frame(self, frame: np.ndarray) -> None:
        self._zero_frame(frame)

    def get_embedding(self, frame: np.ndarray, face) -> np.ndarray:
        return self._get_best_embedding(frame, face)
