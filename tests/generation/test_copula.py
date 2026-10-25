# tests/generation/test_copula.py
import numpy as np

from src.core.stats import residual_correlation
from src.generation._copula import generate_correlated


def series(with_wiggle: bool):
    steps = np.arange(20)
    wiggle = np.array([0, 2, -1, 3, -2, 1, 4, -3, 2, -1, 1, 2, -2, 3, -1, 0, 2, -2, 1, 0], dtype=float)
    x = steps * 5 + (wiggle if with_wiggle else np.zeros(20))
    y = steps * 3 + (wiggle * 0.8 if with_wiggle else np.zeros(20))
    return list(x), list(y)


def test_real_co_movement_is_kept(cfg, rng):
    x, y = series(True)
    result = generate_correlated(x, y, cfg.thresholds, rng)
    assert result.met and result.attempts >= 1
    assert residual_correlation(result.x, result.y, cfg.thresholds)[0] == "positive"
    for new, old in zip(result.x, x):
        if old:
            assert abs(new - old) / abs(old) >= 0.05


def test_a_weak_relationship_is_not_strengthened(cfg, rng):
    x = list(np.linspace(100, 200, 20))
    y = list(np.linspace(50, 60, 20))
    result = generate_correlated(x, y, cfg.thresholds, rng)
    assert result.attempts == 0 and result.met is False


def test_too_few_pairs_is_unmet(cfg, rng):
    result = generate_correlated([1.0, 2.0], [2.0, 1.0], cfg.thresholds, rng)
    assert result.met is False and result.attempts == 0
