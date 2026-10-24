# src/inference/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    seed: int = 12345
    thresholds: dict = None
    paths: dict = None
    model_names: dict = None
    max_repairs: int = 3

    def __post_init__(self):
        if self.thresholds is None:
            self.thresholds = {
                "key_uniqueness": 0.98,
                "fk_overlap": 0.8,
                "constraint_strong": 0.95,
                "constraint_hypo": 0.80,
                "constraint_min_rows": 5,
                "trend_tau_min": 0.5,
                "trend_p_max": 0.10,
                "trend_min_n": 4,
                "corr_min_n": 8,
                "corr_shrink": 0.3,
                "seasonality_min_cycles": 2,
                "agent_conf_threshold": 0.7,
                "k_anonymity": 5,
            }
        if self.paths is None:
            root = Path(__file__).resolve().parents[2]
            models_dir = Path("D:/models") if Path("D:/").exists() else root / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            self.paths = {
                "root": str(root),
                "artifacts": str(root / "artifacts"),
                "models": str(models_dir),
                "cache": str(models_dir / "cache"),
            }
        if self.model_names is None:
            self.model_names = {
                "domain_llm": None,  # set via env if using local LLM
                "agent_llm": None,
            }

    @classmethod
    def from_env(cls) -> "Config":
        seed = int(os.environ.get("DECRAFT_SEED", "12345"))
        return cls(seed=seed)
