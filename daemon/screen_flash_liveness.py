"""screen_flash_liveness.py — Active screen-flash reflectance liveness check.

PROTOTYPE. The decision math below is implemented and unit-tested against
synthetic data (see tests/daemon/test_screen_flash_liveness.py). It has NOT
been validated against a real camera/screen — that needs physical hardware
testing this environment cannot perform. Do not wire this into the live
unlock decision path (main.py) without that validation first.

Core idea: deliberately vary the laptop's own screen luminance in a
randomized, runtime-generated pattern and check whether the face ROI's
observed brightness correlates with it. A real face near the screen, in
low-to-moderate ambient light, picks up a measurable, correlated amount of
that light. A pre-recorded video replay (from a phone or another screen)
has no way to react to a pattern this code only decides on at runtime —
defeating it needs live, interactive video generation in response to the
exact flash sequence, not "a good enough recorded clip". This is a
fundamentally different class of defense than every other layer in
liveness_detector.py, all of which are passive (ask "does this frame look
real") and can in principle be satisfied by a sufficiently good recording.

Not a replacement for the existing stack — a complementary active check,
intended as a periodic or high-stakes-moment supplement (e.g. right before
granting ACTIVE from a cold SCANNING approach), not a per-frame layer,
given it requires deliberately controlling the display.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


def generate_flash_sequence(n_steps: int, rng: Optional[random.Random] = None) -> list[float]:
    """Randomized sequence of screen luminance levels (0.0-1.0) to display,
    one per capture step. Randomized rather than fixed so there is no
    pattern an attacker could precompute a reaction to in advance, even in
    principle — each session's sequence is unique and unknowable ahead of
    time. Deliberately avoids very-low-contrast consecutive steps (min gap
    below), since two nearly-identical luminance levels in a row make the
    expected face-brightness response too small to distinguish from noise."""
    if n_steps < 2:
        raise ValueError("n_steps must be >= 2 to compute a correlation")
    r = rng or random.Random()
    min_gap = 0.3
    seq = [r.uniform(0.1, 0.9)]
    for _ in range(n_steps - 1):
        prev = seq[-1]
        candidates = [v / 100.0 for v in range(10, 91) if abs(v / 100.0 - prev) >= min_gap]
        seq.append(r.choice(candidates) if candidates else r.uniform(0.1, 0.9))
    return seq


@dataclass(frozen=True)
class ReflectanceResult:
    correlation: float
    best_lag: int
    consistent: bool


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    if a_arr.std() < 1e-9 or b_arr.std() < 1e-9:
        # A genuinely constant sequence (observed brightness never changed
        # at all) cannot correlate with anything by definition — treat as
        # zero correlation (not-live), never as an error or an accidental
        # perfect match via a degenerate 0/0 computation.
        return 0.0
    return float(np.corrcoef(a_arr, b_arr)[0, 1])


def score_reflectance(
    screen_sequence: Sequence[float],
    observed_brightness: Sequence[float],
    *,
    max_lag: int = 2,
    min_correlation: float = 0.5,
) -> ReflectanceResult:
    """Correlates the known screen-luminance sequence against observed
    face-ROI brightness, searching a small lag window to account for
    plausible camera/display pipeline latency (a few frames), and takes the
    best-correlating lag rather than lag=0 specifically — a real physical
    response is not required to be instantaneous, but it does need to be
    consistently and repeatably lagged in the SAME direction (screen change
    precedes face-brightness change), which searching only positive lags
    already encodes.

    NOT validated against real camera timing — max_lag=2 and
    min_correlation=0.5 are reasoned defaults, not measured ones."""
    n = len(screen_sequence)
    if len(observed_brightness) != n:
        raise ValueError("screen_sequence and observed_brightness must be the same length")
    if n < 2:
        raise ValueError("need at least 2 samples to compute a correlation")

    best_corr = -1.0
    best_lag = 0
    for lag in range(0, min(max_lag, n - 1) + 1):
        if lag == 0:
            corr = _pearson(screen_sequence, observed_brightness)
        else:
            corr = _pearson(screen_sequence[: n - lag], observed_brightness[lag:])
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    return ReflectanceResult(
        correlation=best_corr,
        best_lag=best_lag,
        consistent=best_corr >= min_correlation,
    )
