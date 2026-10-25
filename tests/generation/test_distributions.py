# tests/generation/test_distributions.py
import numpy as np

from src.generation._distributions import fit_family, sample_numeric


def test_positive_skewed_data_uses_lognormal():
    assert fit_family(np.array([1.0, 2.0, 3.0, 4.0, 40.0])) == "lognormal"


def test_symmetric_data_uses_normal():
    assert fit_family(np.array([1.0, 2.0, 3.0, 4.0, 5.0])) == "normal"


def test_samples_move_away_from_the_source_and_stay_in_range(rng):
    values = [10.0, 12.0, 11.0, 13.0, 12.5, 11.5]
    out = sample_numeric(values, len(values), rng, min_distance=0.05)
    assert all(new != old for new, old in zip(out, values))
    assert all(4.0 <= v <= 20.0 for v in out)


def test_missing_values_are_skipped(rng):
    assert sample_numeric([None, None], 2, rng) == [None, None]
