"""
Standalone UI test — camera-free visual review of Dynamic Island sequences.

Sends states through the real named pipe to exercise:
  idle → scanning → active (triggers verified→welcome→fade) → idle

Usage:
    1. Start daemon (or fake_pipe_sender.py) first.
    2. Start the UI:  python ui/main.py
    3. Run this:       python ui/test_ui_standalone.py

The test uses the daemon's IPCServer for real pipe communication.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
sys.path.insert(0, str(DAEMON_DIR))

from ipc_server import IPCServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("MajestyGuard.UITest")


def main() -> int:
    server = IPCServer()
    server.start()
    log.info("Standalone UI test — press Ctrl+C to stop")

    # ── Test sequence ──
    sequence = [
        # (state payload, display time in seconds, description)
        ({"state": "idle"}, 3.0, "Idle — minimal notch pill"),
        ({"state": "scanning"}, 4.0, "Scanning — dot-pulse animation"),
        ({"state": "active", "confidence": 0.91, "liveness": 0.87}, 5.0,
         "Active → verified checkmark → welcome → pill fade"),
        ({"state": "idle"}, 2.0, "Back to idle"),

        # Lock states for visual comparison
        ({"state": "soft_locked"}, 3.0, "Soft lock — shield mode"),
        ({"state": "verifying_lock"}, 3.0, "Verifying — face scan glyph"),
        ({"state": "active", "confidence": 0.95, "liveness": 0.92}, 5.0,
         "Unlock → verified→welcome→fade + overlay dissolve"),
        ({"state": "idle"}, 2.0, "Final idle"),
    ]

    try:
        for payload, delay, desc in sequence:
            log.info("→ %s  (%.1fs)", desc, delay)
            server.broadcast_state(**payload)
            time.sleep(delay)
        log.info("Test sequence complete.")
    except KeyboardInterrupt:
        log.info("Interrupted.")
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
