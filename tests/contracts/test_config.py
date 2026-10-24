from src.config import load_config


def test_default_yaml_loads_with_agreed_defaults():
    cfg = load_config()
    th = cfg.thresholds
    assert cfg.seed == 42 and th.llm_retries == 3 and th.max_repairs == 3
    assert (th.contradicted_match, th.formula_hypothesis_match, th.formula_strong_match) == (0.50, 0.80, 0.95)
    assert th.min_rows_corr == 8 and th.trend_tau_min == 0.5 and th.trend_p_max == 0.10
