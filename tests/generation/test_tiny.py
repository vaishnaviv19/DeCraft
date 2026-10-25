# tests/generation/test_tiny.py
from src.generation._tiny import jitter_cells, safe_level_factor


def test_safe_factor_never_falls_in_the_exclusion_zone(rng):
    factors = [safe_level_factor(rng) for _ in range(500)]
    assert all(0.60 <= f <= 1.60 for f in factors)
    assert all(abs(f - 1.0) >= 0.05 for f in factors)


def test_every_tiny_table_cell_moves_by_at_least_five_percent(rng):
    for _ in range(50):
        generated = jitter_cells([100.0, -50.0, 20.0], rng)
        for new, old in zip(generated, [100.0, -50.0, 20.0]):
            assert abs(new - old) / abs(old) >= 0.05


def test_jitter_is_reproducible_with_the_same_seed():
    import numpy as np
    a = jitter_cells([10.0, 20.0], np.random.default_rng(7))
    b = jitter_cells([10.0, 20.0], np.random.default_rng(7))
    assert a == b


def test_missing_and_zero_cells_are_handled(rng):
    out = jitter_cells([None, 0.0], rng)
    assert out[0] is None and out[1] > 0
