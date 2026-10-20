"""offline_replay_harness.py — Offline replay attack simulator and soak testing harness.

Provides tools to feed synthetic or recorded frame sequences through MajestyGuard's
liveness and face-recognition layers to verify state transitions, replay taint persistence,
and bounded buffer memory invariants under sustained operation.
"""
from dataclasses import dataclass, field
import time
from typing import Callable, Iterable, List, Optional, Tuple
import numpy as np


@dataclass
class SoakMetrics:
    total_frames: int = 0
    replay_flagged_count: int = 0
    scores: List[float] = field(default_factory=list)
    state_transitions: List[Tuple[int, str]] = field(default_factory=list)
    max_deque_sizes: dict = field(default_factory=dict)


class OfflineReplayHarness:
    """Offline test harness to simulate camera feeds and verify soak invariants."""

    def __init__(self, detector_or_engine=None):
        self.target = detector_or_engine

    @staticmethod
    def generate_synthetic_frames(
        count: int,
        width: int = 640,
        height: int = 480,
        pattern: str = "static",
        noise_std: float = 0.0,
    ) -> Iterable[np.ndarray]:
        """Generate a deterministic synthetic sequence of frames.

        Patterns:
          - 'static': Identical frame repeated (static photo / frozen webcam spoof).
          - 'noise': Static base frame with additive Gaussian pixel noise.
          - 'pulse': Subtle global brightness oscillation simulating blood perfusion.
        """
        base = np.full((height, width, 3), 128, dtype=np.uint8)
        # Add a mock face-like oval in the center
        cv_y, cv_x = height // 2, width // 2
        y, x = np.ogrid[:height, :width]
        mask = ((x - cv_x) ** 2) / (80 ** 2) + ((y - cv_y) ** 2) / (100 ** 2) <= 1
        base[mask] = [160, 180, 200]  # Skin-like BGR patch

        rng = np.random.RandomState(42)

        for i in range(count):
            if pattern == "static":
                yield base.copy()
            elif pattern == "noise":
                noise = rng.normal(0, noise_std, base.shape).astype(np.int16)
                noisy = np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)
                yield noisy
            elif pattern == "pulse":
                factor = 1.0 + 0.03 * np.sin(2 * np.pi * i / 15.0)
                pulsed = np.clip(base.astype(np.float32) * factor, 0, 255).astype(np.uint8)
                yield pulsed
            else:
                yield base.copy()

    def run_soak(
        self,
        frame_generator: Iterable[np.ndarray],
        face_factory: Optional[Callable[[np.ndarray], any]] = None,
        process_callback: Optional[Callable[[np.ndarray, any, int], None]] = None,
    ) -> SoakMetrics:
        """Feed frames through the target and track metrics and deque bounds."""
        metrics = SoakMetrics()

        for idx, frame in enumerate(frame_generator):
            metrics.total_frames += 1
            face = face_factory(frame) if face_factory else None

            if hasattr(self.target, "score_fast") and face is not None:
                score = self.target.score_fast(frame, face)
                metrics.scores.append(score)
            elif hasattr(self.target, "score") and face is not None:
                score = self.target.score(frame, face)
                metrics.scores.append(score)

            if hasattr(self.target, "replay_flagged_this_session"):
                if self.target.replay_flagged_this_session():
                    metrics.replay_flagged_count += 1

            if process_callback:
                process_callback(frame, face, idx)

            # Record max deque sizes to assert memory bounding
            self._record_deque_bounds(metrics)

        return metrics

    def _record_deque_bounds(self, metrics: SoakMetrics) -> None:
        """Inspect all deques on the target and track their maximum observed sizes."""
        if self.target is None:
            return

        for attr_name, attr_val in vars(self.target).items():
            if hasattr(attr_val, "__class__") and attr_val.__class__.__name__ == "deque":
                curr_size = len(attr_val)
                prev_max = metrics.max_deque_sizes.get(attr_name, 0)
                if curr_size > prev_max or attr_name not in metrics.max_deque_sizes:
                    metrics.max_deque_sizes[attr_name] = max(curr_size, prev_max)
                    # Invariant: a bounded deque must never exceed its maxlen
                    if attr_val.maxlen is not None:
                        assert curr_size <= attr_val.maxlen, (
                            f"Deque {attr_name} exceeded maxlen: {curr_size} > {attr_val.maxlen}"
                        )
