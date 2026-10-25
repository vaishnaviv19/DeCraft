# src/contracts/dataset.py
"""CanonicalDataset: the single validated dataset that narrative, tables and charts read (spec 5.2)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.contracts import ChartSpec, Fact, FieldProfile, Rule, UnmetTarget, ValidationReport
from src.contracts import SCHEMA_VERSION


@dataclass
class CanonicalDataset:
    dataset_id: str
    schema_version: str
    seed: int
    tables: dict[str, pd.DataFrame]          # full precision, base units; source of truth
    display: dict[str, pd.DataFrame]         # rounded and tokenised (missingness) for rendering
    key_columns: dict[str, list[str]]        # primary keys per table
    rules: list[Rule]                        # the HARD rules the data satisfies
    facts: dict[str, Fact]                   # placeholder keys -> values (from display tables)
    charts: dict[str, ChartSpec]             # chart_id -> (table_id, label column, series columns)
    lineage: dict[str, str]                  # 'table.column' -> method or rule that produced it
    validation: ValidationReport
    unmet_targets: list[UnmetTarget] = field(default_factory=list)
    # Traceability: the Field Profiles this dataset was generated from (additive, optional).
    source_profiles: list[FieldProfile] = field(default_factory=list)


def empty_dataset(dataset_id: str = "manual", seed: int = 42,
                  validation: ValidationReport | None = None) -> CanonicalDataset:
    """A minimal dataset for interface tests; generation fills the real one in M5/M8."""
    return CanonicalDataset(
        dataset_id=dataset_id, schema_version=SCHEMA_VERSION, seed=seed, tables={}, display={},
        key_columns={}, rules=[], facts={}, charts={}, lineage={},
        validation=validation or ValidationReport(ok=True))
