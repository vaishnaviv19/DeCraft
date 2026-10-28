# tests/conftest.py

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.llm import LLMGateway


class FakeLLM:
    def __init__(self, replies, delay_s=0.0, model_name="fake"):
        self.replies = list(replies)
        self.delay_s = delay_s
        self.model_name = model_name
        self.calls = []

    def complete(self, system, user, temperature=0.0, max_tokens=4000, images=(), reasoning_effort=None):
        self.calls.append((system, user))
        if self.delay_s:
            import time
            time.sleep(self.delay_s)
        if not self.replies:
            return "{}"
        return self.replies.pop(0)


@pytest.fixture
def cfg():
    """The deterministic path is the default in tests (LLM-off Mode)."""
    return load_config(llm_enabled=False)


@pytest.fixture
def rng(cfg):
    return np.random.default_rng(cfg.seed)


@pytest.fixture
def gateway_for(tmp_path):
    '''gateway_for(FakeLLM([...]), **kw) -> LLMGateway with a private cache directory.'''
    def _make(llm=None, **kw):
        llm = llm or FakeLLM(['{}'])
        return LLMGateway(client=llm, cache_dir=tmp_path / 'llm_cache', **kw)
    return _make


@pytest.fixture
def sales_df():
    units = [120, 80, 150, 95, 110, 70]
    price = [45.0, 50.0, 42.0, 48.0, 46.0, 52.0]
    revenue = [u * p for u, p in zip(units, price)]
    cost = [3900, 2800, 4700, 3300, 3700, 2600]
    return pd.DataFrame({
        'Ref': ['A-101', 'A-102', 'A-103', 'A-104', 'A-105', 'A-106'],
        'Region': ['North', 'South', 'North', 'East', 'South', 'West'],
        'Units': units, 'Unit Price': price, 'Revenue': revenue, 'Cost': cost,
        'Profit': [r - c for r, c in zip(revenue, cost)]})


@pytest.fixture(autouse=True)
def no_live_providers(monkeypatch):
    """Tests never reach a real provider: the keys are removed for every test, so `make_gateways`
    builds no Groq/Cloudflare client and nothing spends the daily quota (2026-10-26: a pipeline test
    with the default config made 27 live calls). Tests that need a model use FakeLLM."""
    for key in ("GROQ_API_KEY", "CF_ACCOUNT_ID", "CF_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
