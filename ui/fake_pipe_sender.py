"""
Fake MajestyGuard UI pipe sender.

Cycles the Dynamic Island through camera-free demo states. This exercises
spring morphs, black material, content choreography, enrollment progress,
diagnostic bars, and reduced-motion compatibility.
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


def main() -> int:
    server = IPCServer()
    states = [
        ({"state": "idle"}, 1.0),
        ({"state": "scanning"}, 1.8),
        ({"state": "enrolling", "progress": 0.08, "detail": "Front neutral"}, 1.2),
        ({"state": "enrolling", "progress": 0.33, "detail": "Left 25 degrees"}, 1.2),
        ({"state": "enrolling", "progress": 0.67, "detail": "Chin slightly up"}, 1.2),
        ({"state": "enrolling", "progress": 1.0, "detail": "Gallery complete"}, 1.4),
        (
            {
                "state": "calibrating",
                "confidence": 0.86,
                "liveness": 0.78,
                "quality": 0.82,
                "face_position": 0.91,
                "detail": "Lighting stable",
            },
            2.0,
        ),
        ({"state": "active", "confidence": 0.91, "liveness": 0.81}, 2.0),
        ({"state": "stranger"}, 1.4),
        ({"state": "locked"}, 1.0),
        ({"state": "idle"}, 1.0),
    ]

    server.start()
    print("Fake UI pipe sender running. Press Ctrl+C to stop.")
    try:
        while True:
            for payload, delay in states:
                print(payload)
                server.broadcast_state(**payload)
                time.sleep(delay)
    except KeyboardInterrupt:
        print("Stopping fake UI pipe sender.")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
