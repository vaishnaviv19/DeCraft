# src/config.py
"""Single configuration for DeCraft: YAML file -> nested, strict Pydantic models."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # rule evidence (tier bands)
    min_rows_rule: int = Field(5, ge=1)
    formula_strong_match: float = Field(0.95, ge=0, le=1)
    formula_hypothesis_match: float = Field(0.80, ge=0, le=1)
    contradicted_match: float = Field(0.50, ge=0, le=1)
    rel_tol_default: float = Field(0.005, ge=0)
    # correlation and trend
    min_rows_corr: int = Field(8, ge=2)
    corr_tolerance_small_n: float = Field(0.25, ge=0, le=2)
    trend_min_points: int = Field(5, ge=3)
    trend_tau_min: float = Field(0.5, ge=0, le=1)
    trend_p_max: float = Field(0.10, ge=0, le=1)
    # structural breaks
    break_min_points: int = Field(6, ge=4)
    break_min_persist: int = Field(2, ge=1)
    break_size_mult: float = Field(3.0, gt=0)
    break_bic_margin: float = Field(10.0, ge=0)
    break_p_max: float = Field(0.05, ge=0, le=1)
    flat_pct: float = Field(0.05, ge=0)
    # LLM, repair and generation
    llm_retries: int = Field(3, ge=0)
    llm_confidence_escalate: float = Field(0.70, ge=0, le=1)
    # an LLM label/method that is LESS protective than what Python measured or the policy chose is
    # used only at or above this confidence (with a reason); 0.70 above is enough to add protection
    llm_override_confidence: float = Field(0.90, ge=0, le=1)
    max_repairs: int = Field(3, ge=0)
    max_generation_tries: int = Field(50, ge=1)
    ngram_overlap_max: int = Field(8, ge=1)
    # small tables and privacy distance
    tiny_table_max_rows: int = Field(4, ge=1)
    jitter_low: float = Field(0.60, gt=0)
    jitter_high: float = Field(1.60, gt=0)
    min_value_distance: float = Field(0.05, ge=0, lt=1)
    min_headline_distance: float = Field(0.10, ge=0, lt=1)
    k_anonymity_min_rows: int = Field(20, ge=1)
    k_anonymity_k: int = Field(3, ge=2)
    # utility gate
    utility_range_widen: float = Field(0.60, ge=0)
    utility_category_share_tol: float = Field(0.15, ge=0, le=1)
    utility_strong_corr: float = Field(0.50, ge=0, le=1)

    @model_validator(mode="after")
    def _bands_are_ordered(self) -> "Thresholds":
        if not (self.contradicted_match < self.formula_hypothesis_match < self.formula_strong_match):
            raise ValueError("need contradicted_match < formula_hypothesis_match < formula_strong_match")
        if not (self.jitter_low < 1 - self.min_value_distance and self.jitter_high > 1 + self.min_value_distance):
            raise ValueError("jitter band must leave room outside the +/- min_value_distance exclusion zone")
        return self


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    llm_timeout_seconds: float = Field(45, gt=0)
    llm_max_output_tokens: int = Field(4000, gt=0)
    llm_max_concurrency: int = Field(2, ge=1)
    stage_token_budget: int = Field(200_000, gt=0)
    stage_timeout_seconds: float = Field(600, gt=0)


# ---------------------------------------------------------------- v3.1 providers and report settings
class ProviderLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rpm: int = Field(30, ge=1)
    tpm: int = Field(8000, ge=1)
    rpd: int = Field(1000, ge=1)
    tpd: int = Field(200_000, ge=1)


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = "groq"
    model: str = "openai/gpt-oss-120b"
    max_concurrency: int = Field(2, ge=1)
    limits: ProviderLimits = Field(default_factory=ProviderLimits)


class ImageProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = "cloudflare"
    model: str = "@cf/black-forest-labs/flux-1-schnell"
    images_per_day: int = Field(20, ge=0)
    steps: int = Field(4, ge=1)


class Providers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: ProviderSettings = Field(default_factory=ProviderSettings)
    vision: ProviderSettings = Field(default_factory=lambda: ProviderSettings(
        kind="cloudflare", model="@cf/meta/llama-3.2-11b-vision-instruct", max_concurrency=1,
        limits=ProviderLimits(rpm=10, tpm=100_000, rpd=80, tpd=400_000)))
    image: ImageProviderSettings = Field(default_factory=ImageProviderSettings)


class ReportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    period_shift_years: int = 1
    word_tolerance: float = Field(0.15, ge=0, lt=1)
    min_total_words: int = Field(1000, ge=1)
    row_tolerance: float = Field(0.20, ge=0, lt=1)
    row_padding_compress_max: float = Field(0.15, ge=0, lt=1)
    critic_rounds: int = Field(2, ge=0)
    critic_param_range: float = Field(0.20, ge=0, lt=1)
    image_retries: int = Field(2, ge=0)
    shorten_retries: int = Field(2, ge=0)
    reconcile_rounds: int = Field(2, ge=0)
    writer_retries: int = Field(2, ge=0)
    render_dpi: int = Field(110, ge=36)
    image_max_px: int = Field(1024, ge=64)


class ExtractionSettings(BaseModel):
    """E-37: every run parses its own input PDF."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    method: Literal["auto", "docling", "pymupdf"] = "auto"
    cache_dir: str = "artifacts/extraction_cache"     # relative paths are under the repo root
    models_dir: str | None = None                     # else env DOCLING_ARTIFACTS_PATH, else docling's default
    timeout_seconds: float = Field(1800, gt=0)        # docling needs minutes on CPU


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    config_version: str = "2.0"
    seed: int = 42
    llm_enabled: bool = True
    artifacts_dir: str = "artifacts"
    thresholds: Thresholds = Field(default_factory=Thresholds)
    budgets: Budgets = Field(default_factory=Budgets)
    providers: Providers = Field(default_factory=Providers)
    report: ReportSettings = Field(default_factory=ReportSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)

    def config_hash(self) -> str:
        """sha256 of the canonical JSON form; recorded in every RunManifest."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


def load_config(path: str | Path | None = None, **overrides) -> Config:
    """Load YAML (default: config/default.yaml), apply top-level overrides, validate strictly."""
    data = yaml.safe_load(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")) or {}
    data.update(overrides)
    return Config.model_validate(data)
