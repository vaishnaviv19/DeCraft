# tests/core/test_stats.py
import numpy as np

from src.core.stats import classify_trend, residual_correlation, theil_sen


def test_rising_falling_and_tiny(cfg):
    th = cfg.thresholds
    assert classify_trend([1, 2, 3, 4, 5, 6], th)[0] == "up"
    assert classify_trend([6, 5, 4, 3, 2, 1], th)[0] == "down"
    assert classify_trend([1, 2], th)[0] == "uncertain"


def test_a_rising_trend_may_contain_dips(cfg):
    assert classify_trend([1, 2, 1.5, 3, 2.8, 4, 4.2, 5], cfg.thresholds)[0] == "up"


def test_flat_series(cfg):
    assert classify_trend([5, 5, 5, 5, 5, 5], cfg.thresholds)[0] == "flat"


def test_theil_sen_slope_is_robust_to_a_spike(cfg):
    values = np.array([1, 2, 3, 4, 100, 6, 7], dtype=float)
    slope, _ = theil_sen(values)
    assert 0.8 <= slope <= 1.2


def test_real_co_movement_survives_detrending(cfg, rng):
    steps = np.arange(20)
    noise = rng.normal(0, 1, 20)
    x = steps + noise
    y = steps * 2 + noise                       # same wiggle, different trend
    direction, rho, p, uncertain = residual_correlation(x, y, cfg.thresholds)
    assert direction == "positive" and rho > 0.5 and not uncertain


def test_two_unrelated_trending_series_are_not_correlated(cfg, rng):
    steps = np.arange(20)
    x = steps + rng.normal(0, 1, 20)
    y = steps[::-1] + rng.normal(0, 1, 20)      # opposite trends, independent noise
    assert residual_correlation(x, y, cfg.thresholds)[0] in {"none", "uncertain"}


def test_too_few_pairs_is_uncertain(cfg):
    assert residual_correlation([1, 2, 3], [3, 2, 1], cfg.thresholds)[0] == "uncertain"
