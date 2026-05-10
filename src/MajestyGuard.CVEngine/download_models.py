# MajestyGuard.CVEngine/download_models.py
# Downloads all required models for MajestyGuard.
# Run this during installation (called by Install.ps1).
#
# MODELS DOWNLOADED:
#   1. InsightFace buffalo_l (~300MB) — face detection + recognition
#   2. MiniFASNetV2 ONNX (~600KB)    — anti-spoof liveness Layer 7
#
# ALL downloads are from official public repos.
# After download, internet access is blocked by the firewall rule.

import os
import sys
import urllib.request
import hashlib
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("MajestyGuard.ModelDownloader")

# ── Model definitions ──────────────────────────────────────────────

MODELS = {
    "buffalo_l": {
        "description": "InsightFace buffalo_l (face detection + recognition)",
        "auto_download": True,  # InsightFace handles this itself
        "size_mb": 300,
    },
    "antispoof_minifasv2": {
        "description": "MiniFASNetV2 anti-spoofing model (600KB)",
        "url": (
            "https://github.com/facenox/face-antispoof-onnx"
            "/releases/download/v1.0.0/best_model.onnx"
        ),
        "filename": "antispoof_minifasv2.onnx",
        "sha256": "af2381b88f38769222ed93379e12444e2a50814575de1c46170de570c55a42b6",
        "size_mb": 0.6,
    },
}


def download_file(url: str, dest_path: str, description: str, expected_sha256: str = None) -> bool:
    """Download with progress indicator and optional SHA256 verification."""
    logger.info("Downloading %s...", description)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        def reporthook(count, block_size, total_size):
            if total_size > 0:
                pct = min(100, count * block_size * 100 // total_size)
                sys.stdout.write(f"\r  {pct}% ")
                sys.stdout.flush()

        urllib.request.urlretrieve(url, dest_path, reporthook)
        sys.stdout.write("\r  100%\n")

        # Verify hash if provided
        if expected_sha256:
            logger.info("  Verifying checksum...")
            sha256_hash = hashlib.sha256()
            with open(dest_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            
            actual_sha256 = sha256_hash.hexdigest().lower()
            if actual_sha256 != expected_sha256.lower():
                logger.error("  Verification FAILED!")
                logger.error("  Expected: %s", expected_sha256)
                logger.error("  Actual:   %s", actual_sha256)
                os.remove(dest_path)
                return False
            logger.info("  Checksum verified ✓")

        logger.info("  Saved to: %s", dest_path)
        return True

    except Exception as e:
        logger.error("  Download failed: %s", e)
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def download_insightface_model(model_dir: str) -> bool:
    """InsightFace downloads buffalo_l automatically on first use."""
    logger.info("Pre-downloading InsightFace buffalo_l model...")
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(
            name="buffalo_l",
            root=model_dir,
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=0, det_size=(160, 160))
        logger.info("  buffalo_l ready.")
        return True
    except ImportError:
        logger.error("  InsightFace not installed. Run: pip install insightface")
        return False
    except Exception as e:
        logger.error("  buffalo_l download failed: %s", e)
        return False


def download_antispoof_model(model_dir: str) -> bool:
    """Download MiniFASNetV2 ONNX anti-spoof model."""
    info = MODELS["antispoof_minifasv2"]
    dest = os.path.join(model_dir, info["filename"])

    if os.path.exists(dest):
        logger.info("  Anti-spoof model already present: %s", dest)
        return True

    return download_file(info["url"], dest, info["description"], info["sha256"])


def main():
    model_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "models"
    )

    logger.info("=" * 55)
    logger.info("  MajestyGuard Model Downloader")
    logger.info("  Model directory: %s", model_dir)
    logger.info("=" * 55)

    results = {}
    results["buffalo_l"]   = download_insightface_model(model_dir)
    results["antispoof"]   = download_antispoof_model(model_dir)

    logger.info("")
    logger.info("Download summary:")
    for name, ok in results.items():
        status = "✓" if ok else "✗ FAILED"
        logger.info("  %s  %s", status, name)

    if not all(results.values()):
        logger.error("Some models failed to download. Check your internet connection.")
        sys.exit(1)

    logger.info("")
    logger.info("All models ready. MajestyGuard CV engine can now start.")


if __name__ == "__main__":
    main()
