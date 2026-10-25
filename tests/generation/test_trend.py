# tests/generation/test_trend.py
import numpy as np

from src.core.segments import detect_segments
from src.core.stats import classify_trend
from src.generation._trend import generate_with_segments, generate_with_shape


def test_generated_series_keeps_the_direction_but_not_the_values(cfg, rng):
    source = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]
    result = generate_with_shape(source, "up", cfg.thresholds, rng)
    assert result.met is True and result.attempts >= 1
    assert classify_trend(result.values, cfg.thresholds)[0] == "up"
    for new, old in zip(result.values, source):
        assert abs(new - old) / abs(old) >= 0.05


def test_a_falling_series_stays_falling(cfg, rng):
    source = [150.0, 140.0, 130.0, 120.0, 110.0, 100.0]
    result = generate_with_shape(source, "down", cfg.thresholds, rng)
    assert result.met and classify_trend(result.values, cfg.thresholds)[0] == "down"


def test_a_series_too_short_to_shape_is_unmet(cfg, rng):
    result = generate_with_shape([1.0], "up", cfg.thresholds, rng)
    assert result.met is False and result.values == [1.0]


def test_segment_shape_is_kept(cfg, rng):
    crash = [1, 2, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    shape = detect_segments(crash, cfg.thresholds, np.random.default_rng(1))
    assert shape.directions == ["up", "down", "up"]
    result = generate_with_segments([float(v) for v in crash], shape, cfg.thresholds, rng)
    assert result.met and result.attempts >= 1
    assert detect_segments(result.values, cfg.thresholds, None, 0).directions == ["up", "down", "up"]
