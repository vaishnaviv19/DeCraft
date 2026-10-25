import pytest
from pydantic import ValidationError
from src.config import Config, Thresholds, load_config


def test_default_yaml_loads_with_agreed_defaults():
    cfg = load_config()
    th = cfg.thresholds
    assert cfg.seed == 42 and th.llm_retries == 3 and th.max_repairs == 3
    assert (th.contradicted_match, th.formula_hypothesis_match, th.formula_strong_match) == (0.50, 0.80, 0.95)
    assert th.min_rows_corr == 8 and th.trend_tau_min == 0.5 and th.trend_p_max == 0.10


def test_yaml_and_code_defaults_agree():
    assert load_config() == Config()


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        load_config(unknown_option=True)


def test_band_order_is_enforced():
    with pytest.raises(ValidationError):
        Thresholds(formula_hypothesis_match=0.97)


def test_config_hash_changes_with_values():
    assert load_config().config_hash() != load_config(seed=7).config_hash()
