# tests/generation/test_keys.py
import pandas as pd

from src.generation._keys import fresh_key_values


def test_fresh_keys_are_disjoint_from_the_source_and_unique(rng):
    source = pd.Series(["A-101", "A-102", "A-103"])
    out = fresh_key_values(source, 5, rng)
    assert not set(out) & set(source)
    assert len(set(out)) == 5
    assert all(value.startswith("A-") for value in out)


def test_format_and_width_are_preserved(rng):
    out = fresh_key_values(pd.Series(["CUST_0001", "CUST_0002"]), 3, rng)
    assert all(len(v) == len("CUST_0001") and v.startswith("CUST_") for v in out)


def test_keys_without_digits_get_a_last_resort_id_format(rng):
    # normally the privacy plan relabels such keys with believable fakes; this is the fallback
    out = fresh_key_values(pd.Series(["North", "South"]), 4, rng)
    assert len(set(out)) == 4 and all(v.startswith("ID-") for v in out)
    assert not set(out) & {"North", "South"}


def test_every_digit_shape_is_kept(rng):
    out = fresh_key_values(pd.Series(["INV-2024-001", "INV-2024-002"]), 3, rng)
    assert all(v.startswith("INV-2024-") and len(v) == len("INV-2024-001") for v in out)
    numbers = fresh_key_values(pd.Series([5001.0, 5002.0, 5003.0]), 3, rng)
    assert all(isinstance(v, int) and v > 5003 and len(str(v)) == 4 for v in numbers)
