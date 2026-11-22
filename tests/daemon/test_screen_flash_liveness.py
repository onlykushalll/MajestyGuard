"""Tests for the screen-flash reflectance prototype (screen_flash_liveness.py).

These validate the decision math against synthetic data only. They do NOT
validate real camera/screen timing or actual reflectance magnitude — see
the module docstring.
"""
import random

import numpy as np
import pytest

from daemon import screen_flash_liveness
from daemon.screen_flash_liveness import (
    generate_flash_sequence,
    score_reflectance,
)


def test_generate_flash_sequence_has_no_low_contrast_consecutive_steps():
    rng = random.Random(1234)
    seq = generate_flash_sequence(30, rng=rng)
    assert len(seq) == 30
    for a, b in zip(seq, seq[1:]):
        assert abs(a - b) >= 0.3
    assert all(0.0 <= v <= 1.0 for v in seq)


def test_generate_flash_sequence_is_randomized_not_fixed():
    seq_a = generate_flash_sequence(20, rng=random.Random(1))
    seq_b = generate_flash_sequence(20, rng=random.Random(2))
    assert seq_a != seq_b


def test_generate_flash_sequence_rejects_too_few_steps():
    with pytest.raises(ValueError):
        generate_flash_sequence(1)


def test_default_flash_sequence_uses_system_random(monkeypatch):
    created = []

    class FixedSystemRandom:
        def uniform(self, low, high):
            return low

        def choice(self, candidates):
            return candidates[-1]

    def make_rng():
        rng = FixedSystemRandom()
        created.append(rng)
        return rng

    monkeypatch.setattr(screen_flash_liveness.random, "SystemRandom", make_rng)

    sequence = generate_flash_sequence(4)

    assert len(created) == 1
    assert len(sequence) == 4


@pytest.mark.parametrize("lag", [0, 1, 2])
def test_reactive_signal_is_detected_as_consistent_even_with_realistic_noise(lag):
    rng = np.random.default_rng(42)
    screen = generate_flash_sequence(24, rng=random.Random(7))
    # Simulate a real, physically-lagged, noisy response: shifted by `lag`,
    # scaled down (screen light is one of several contributions to total
    # face brightness, not the only one), plus realistic sensor/ambient noise.
    delayed = [screen[max(0, i - lag)] for i in range(len(screen))]
    observed = [0.4 * v + rng.normal(0, 0.06) for v in delayed]

    result = score_reflectance(screen, observed, max_lag=2, min_correlation=0.5)
    assert result.consistent, f"correlation={result.correlation:.3f} should have cleared 0.5"
    assert result.correlation > 0.5


def test_unrelated_signal_is_correctly_rejected():
    rng = np.random.default_rng(99)
    screen = generate_flash_sequence(24, rng=random.Random(3))
    # A video replay's own brightness has no relationship to a sequence
    # this code only decided on at runtime.
    observed = list(rng.uniform(0.2, 0.8, size=len(screen)))

    result = score_reflectance(screen, observed, max_lag=2, min_correlation=0.5)
    assert not result.consistent, f"correlation={result.correlation:.3f} should NOT have cleared 0.5"


def test_constant_observed_brightness_is_never_falsely_consistent():
    screen = generate_flash_sequence(20, rng=random.Random(5))
    observed = [0.5] * len(screen)  # zero variance: a flat, unreactive surface
    result = score_reflectance(screen, observed)
    assert result.correlation == 0.0
    assert not result.consistent


def test_perfect_zero_lag_match_scores_near_one():
    screen = generate_flash_sequence(20, rng=random.Random(11))
    result = score_reflectance(screen, screen, max_lag=0)
    assert result.correlation > 0.999
    assert result.best_lag == 0
    assert result.consistent


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        score_reflectance([0.1, 0.2, 0.3], [0.1, 0.2])


def test_too_few_samples_raise():
    with pytest.raises(ValueError):
        score_reflectance([0.1], [0.2])


def test_repeated_runs_across_many_random_seeds_hold_up():
    # Single-seed tests can pass by luck. Sweep many seeds for both classes
    # and require a strong majority correct, not just one lucky draw.
    reactive_correct = 0
    unrelated_correct = 0
    n_trials = 40
    for seed in range(n_trials):
        rng = np.random.default_rng(seed)
        screen = generate_flash_sequence(24, rng=random.Random(seed + 1000))

        reactive = [0.4 * v + rng.normal(0, 0.07) for v in screen]
        if score_reflectance(screen, reactive).consistent:
            reactive_correct += 1

        unrelated = list(rng.uniform(0.2, 0.8, size=len(screen)))
        if not score_reflectance(screen, unrelated).consistent:
            unrelated_correct += 1

    assert reactive_correct >= n_trials * 0.9, f"only {reactive_correct}/{n_trials} reactive signals detected"
    assert unrelated_correct >= n_trials * 0.9, f"only {unrelated_correct}/{n_trials} unrelated signals rejected"
