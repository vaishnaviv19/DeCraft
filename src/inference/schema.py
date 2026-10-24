# src/inference/schema.py
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class RuleKind(str, Enum):
    EXPLICIT = "explicit"
    STRONG = "strongly_inferred"
    SEMANTIC = "semantic"
    HYPOTHESIS = "hypothesized"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    source: str  # header | caption | cell_format | cross_table | stat_test
    detail: str
    weight: float = Field(ge=0, le=1)


class FieldProfile(BaseModel):
    table: str
    name: str
    physical_type: str
    semantic_type: str
    n: int
    missing_rate: float
    n_unique: int
    min: Optional[float] = None
    max: Optional[float] = None
    unit: Optional[str] = None
    fmt: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    evidence: List[Evidence]

    @field_validator("confidence")
    @classmethod
    def validate_conf(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("confidence must be between 0 and 1")
        return v


class Constraint(BaseModel):
    id: str
    expression: str
    inputs: List[str]
    output: Optional[str] = None
    kind: RuleKind
    severity: str  # 'hard' | 'soft'
    tolerance: float = 0.0
    support_rows: int
    match_rate: float
    confidence: float = Field(ge=0, le=1)
    evidence: List[Evidence]


class Relationship(BaseModel):
    id: str
    parent_table: str
    parent_key: str
    child_table: str
    child_key: str
    cardinality: str  # one_to_one | one_to_many | many_to_many
    overlap_rate: float
    confidence: float = Field(ge=0, le=1)
    evidence: List[Evidence]


class PatternSpec(BaseModel):
    field: str
    kind: str  # trend | correlation | seasonality
    direction: str  # increasing | decreasing | flat | mixed | unknown
    strength: float = Field(ge=0, le=1)
    n: int
    uncertain: bool = False
    evidence: List[Evidence]


class PrivacyDecision(BaseModel):
    field: str
    table: str
    risk: str  # direct_id | quasi_id | rare_combo | outlier | low
    method: str  # fresh_id | categorical_smoothed | numeric_distribution | date_ordered | derived | trend | copula
    rationale: str
    preserve: bool = False


class DomainHypothesis(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)
    evidence: List[Evidence]


class ValidationIssue(BaseModel):
    code: str
    message: str
    table: Optional[str] = None
    field: Optional[str] = None
    row_indices: List[int] = []
    repairable: bool = True


class ValidationReport(BaseModel):
    ok: bool
    issues: List[ValidationIssue] = []
    repairable: bool = True
    stats: Dict[str, Any] = {}


class InferenceReport(BaseModel):
    source_pdf: str
    source_hash: str
    blueprint_version: str
    seed: int
    domain: DomainHypothesis
    fields: List[FieldProfile] = []
    relationships: List[Relationship] = []
    constraints: List[Constraint] = []
    patterns: List[PatternSpec] = []
    privacy: List[PrivacyDecision] = []
    dependency_order: List[str] = []
    lineage: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
