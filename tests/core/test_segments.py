# tests/core/test_segments.py
import numpy as np
import pytest

from src.core.segments import detect_segments


def crash_series() -> list[float]:
    return ([1, 2, 3, 4, 5, 6, 7, 8]
            + [9, 8, 7, 6, 5, 4]
            + [5, 6, 7, 8, 9, 10, 11, 12])


def test_crash_shape_is_found(cfg, rng):
    shape = detect_segments(crash_series(), cfg.thresholds, rng)
    assert shape.tier in {"confirmed", "probable"}
    assert shape.directions == ["up", "down", "up"]
    assert len(shape.break_indices) == 2


def test_a_pure_trend_is_not_a_break(cfg, rng):
    shape = detect_segments(list(range(1, 21)), cfg.thresholds, rng)
    assert shape.tier == "uncertain" and len(shape.segments) == 1


def test_a_single_spike_is_not_a_break(cfg, rng):
    values = list(np.linspace(10, 12, 20))
    values[10] = 40.0
    assert detect_segments(values, cfg.thresholds, rng).tier == "uncertain"


def test_too_few_points_is_uncertain(cfg, rng):
    shape = detect_segments([1.0, 2.0, 3.0], cfg.thresholds, rng)
    assert shape.tier == "uncertain" and len(shape.segments) == 1


@pytest.mark.slow
def test_noise_never_produces_a_break_over_200_seeds(cfg):
    rng = np.random.default_rng(2024)
    breaks = 0
    for _ in range(200):
        noise = rng.normal(0, 1, 20)
        if detect_segments(noise, cfg.thresholds, np.random.default_rng(int(rng.integers(0, 10**6)))
                           ).tier != "uncertain":
            breaks += 1
    assert breaks / 200 <= 0.05
