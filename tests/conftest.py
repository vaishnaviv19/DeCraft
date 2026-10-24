# tests/conftest.py
import tempfile
from pathlib import Path

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

    def complete(self, system, user, temperature=0.0, max_tokens=4000):
        self.calls.append((system, user))
        if self.delay_s:
            import time
            time.sleep(self.delay_s)
        if not self.replies:
            return "{}"
        return self.replies.pop(0)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def rng():
    return np.random.default_rng(123)


@pytest.fixture
def gateway_for(tmp_path):
    def _make(llm=None, **kw):
        llm = llm or FakeLLM(['{}'])
        d = tmp_path / "cache"
        d.mkdir(exist_ok=True)
        return LLMGateway(client=llm, cache_dir=d, **kw)
    return _make


@pytest.fixture
def sales_df():
    return pd.DataFrame({
        'Ref': ['R1', 'R2', 'R3', 'R4', 'R5', 'R6'],
        'Region': ['North', 'South', 'North', 'East', 'West', 'North'],
        'Units': [10, 20, 15, 5, 8, 12],
        'Unit Price': [100.0, 50.0, 80.0, 200.0, 125.0, 90.0],
        'Revenue': [1000.0, 1000.0, 1200.0, 1000.0, 1000.0, 1080.0],
        'Cost': [700.0, 800.0, 900.0, 800.0, 750.0, 810.0],
    })
