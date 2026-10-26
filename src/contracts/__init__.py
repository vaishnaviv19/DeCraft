# src/contracts/__init__.py  -- SHARED KERNEL: data contracts only (no behaviour beyond validation)
"""Every stage boundary reads and writes only these types. Strict: extra fields are rejected.
Change policy: additive optional fields only, with schema_version bump; never rename or repurpose a field."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "2.6"                         # 2.6: RunResult (additive)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- enums
class RuleTier(str, Enum):
    EXPLICIT = "explicit"                    # stated in the PDF text and not contradicted by the data
    STRONGLY_INFERRED = "strongly_inferred"  # match_rate >= formula_strong_match on >= min_rows_rule rows
    SEMANTIC = "semantic"                    # plausible but untestable (too few rows)
    HYPOTHESIZED = "hypothesized"            # formula_hypothesis_match <= match_rate < formula_strong_match
    UNKNOWN = "unknown"                      # contradicted_match <= match_rate < formula_hypothesis_match
    CONTRADICTED = "contradicted"            # match_rate < contradicted_match -> rejected


class DecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Enforcement(str, Enum):
    HARD = "hard"      # must hold exactly (within tolerance); violation -> repair or HardConstraintError
    SOFT = "soft"      # statistical target; unmet -> UnmetTarget recorded, run continues
    NONE = "none"      # recorded in the report only


class RuleKind(str, Enum):
    FORMULA = "formula"
    ROW_FORMULA = "row_formula"       # between rows of one table: [Gross profit] = [Revenue] - [Cost of sales]
    DATE_DERIVATION = "date_derivation"
    DETERMINES = "determines"
    ORDERING = "ordering"
    HIERARCHY = "hierarchy"
    CONDITIONAL = "conditional"
    TOTAL_ROWS = "total_rows"
    AGGREGATE = "aggregate"
    FOREIGN_KEY = "foreign_key"


class ExtractionGrade(str, Enum):
    GOOD = "good"
    DOUBTFUL = "doubtful"
    BAD = "bad"


class MissingKind(str, Enum):
    NULL = "null"                      # blank / unknown
    DASH = "dash"                      # '-' ; may mean zero (see dash_means_zero)
    NA = "na"                          # not applicable
    NM = "nm"                          # not meaningful
    SUPPRESSED_PRIVACY = "suppressed_privacy"
    EXTRACTION_FAILED = "extraction_failed"


class SensitivityClass(str, Enum):
    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    SENSITIVE = "sensitive"
    BUSINESS_METRIC = "business_metric"
    LOW_RISK = "low_risk"
    DERIVED = "derived"


class PatternKind(str, Enum):
    TREND = "trend"
    SEGMENTS = "segments"              # structural breaks
    CORRELATION = "correlation"
    SEASONALITY = "seasonality"
    LAGGED_RELATIONSHIP = "lagged_relationship"
    SEGMENT_PATTERN = "segment_pattern"   # relationship differs by category


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    MIXED = "mixed"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NONE = "none"
    UNCERTAIN = "uncertain"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Cardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


# ---------------------------------------------------------------- references and evidence
class ColumnRef(Strict):
    table_id: str
    column: str

    @property
    def node(self) -> str:
        """Graph node name. Always qualified: 'table.column' (fixes bare-name collisions)."""
        return f"{self.table_id}.{self.column}"


class SourceRef(Strict):
    document_sha256: str
    page: int = Field(ge=1)
    table_id: str | None = None
    row: int | None = None
    column: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class Evidence(Strict):
    source: Literal["header", "caption", "cell_format", "context_text", "cross_table",
                    "stat_test", "llm_proposal", "rule_detector", "extraction"]
    detail: str
    ref: SourceRef | None = None


# ---------------------------------------------------------------- extraction
class TableQuality(Strict):
    grade: ExtractionGrade
    issues: list[str] = []


class SourceTable(Strict):
    """One extracted table. raw_cells keeps the ORIGINAL strings; normalisation happens in inference."""
    table_id: str
    page: int = Field(ge=1)
    caption: str | None = None
    header: list[str]
    raw_cells: list[list[str | None]]
    row_x0: list[float] | None = None            # x position of each row label (indent levels)
    cell_bbox: list[list[tuple[float, float, float, float] | None]] | None = None
    extraction_method: str = "pymupdf"
    ocr: bool = False
    context_text: str = ""
    quality: TableQuality | None = None          # filled by inference.grade_table


class SourceDocument(Strict):
    document_sha256: str
    title: str | None = None
    entity_name: str | None = None               # preserved, never regenerated (decision D-07)
    entity_address: str | None = None            # preserved like the entity name (D-07)
    tables: list[SourceTable]
    text_blocks: list[str] = []                  # narrative text, in reading order


# ---------------------------------------------------------------- inference
class NumericStats(Strict):
    minimum: float
    maximum: float
    mean: float
    quantiles: dict[str, float] = {}


class DateStats(Strict):
    earliest: date
    latest: date


class CategoryStats(Strict):
    frequencies: dict[str, int]


class FieldProfile(Strict):
    table_id: str
    column: str
    physical_type: Literal["integer", "float", "text", "date", "period", "boolean", "empty"]
    semantic_type: str = "unknown"
    unit: str | None = None                      # 'USD', '%', 'kg' ...
    scale: float = 1.0                           # multiplier to base units (A.4)
    decimals: int | None = None                  # most common displayed decimals
    additive: bool | None = None                 # True when source total rows equal the sum of children
    n: int = Field(ge=0)
    n_missing: int = Field(ge=0)
    n_unique: int = Field(ge=0)
    missing_kinds: dict[MissingKind, int] = {}
    numeric: NumericStats | None = None
    dates: DateStats | None = None
    categories: CategoryStats | None = None
    extraction_grade: ExtractionGrade = ExtractionGrade.GOOD
    llm_confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[Evidence] = []

    @model_validator(mode="after")
    def _counts_consistent(self) -> "FieldProfile":
        if self.n_missing > self.n:
            raise ValueError("n_missing > n")
        if self.n_unique > self.n - self.n_missing:
            raise ValueError("n_unique > number of non-missing values")
        return self


class Expr(Strict):
    """Formula expression tree. Built ONLY by inference._formula.parse_formula (never by eval)."""
    op: Literal["col", "const", "add", "sub", "mul", "div", "floordiv", "mod", "neg", "sum", "min", "max",
                "abs", "round", "years_between", "days_between", "pct_change", "ratio_pct"]
    col: str | None = None
    value: float | None = None
    args: list["Expr"] = []


class FormulaSpec(Strict):
    output: str
    expr: Expr
    text: str                                    # canonical text, e.g. "[Revenue] = [Units] * [Unit Price]"

    @property
    def inputs(self) -> list[str]:
        out: list[str] = []
        stack = [self.expr]
        while stack:
            e = stack.pop()
            if e.op == "col" and e.col not in out:
                out.append(e.col)
            stack.extend(reversed(e.args))
        return out


class Rule(Strict):
    rule_id: str
    kind: RuleKind
    table_id: str
    columns: list[str]
    formula: FormulaSpec | None = None
    params: dict[str, Any] = {}
    tier: RuleTier
    status: DecisionStatus
    enforcement: Enforcement
    match_rate: float | None = Field(default=None, ge=0, le=1)
    n_rows: int = Field(0, ge=0)
    rel_tol: float = 0.005
    abs_tol: float = 0.0
    llm_confidence: float | None = Field(default=None, ge=0, le=1)
    extraction_grade: ExtractionGrade = ExtractionGrade.GOOD
    evidence: list[Evidence] = []
    contradicting: list[Evidence] = []
    reason: str                                  # why this tier/status/enforcement (human readable)


class Relationship(Strict):
    rel_id: str
    parent: ColumnRef
    child: ColumnRef
    cardinality: Cardinality
    overlap_rate: float = Field(ge=0, le=1)
    parent_unique_rate: float = Field(ge=0, le=1)
    tier: RuleTier
    status: DecisionStatus
    evidence: list[Evidence] = []


class SegmentOut(Strict):
    start: int
    end: int
    direction: Literal["up", "down", "flat"]
    pct_change: float


class PatternSpec(Strict):
    pattern_id: str
    kind: PatternKind
    fields: list[ColumnRef] = Field(min_length=1)
    time_field: ColumnRef | None = None
    # a series ALONG A ROW of a statement (years across the header): fields[0] is the label column,
    # row_label names the line item and period_columns lists its columns in time order
    row_label: str | None = None
    period_columns: list[str] = []
    segment_field: ColumnRef | None = None
    direction: Direction
    n: int = Field(ge=0)
    statistic_name: str | None = None            # 'kendall_tau', 'spearman_rho_residual', ...
    statistic_value: float | None = None
    p_value: float | None = Field(default=None, ge=0, le=1)
    slope: float | None = None                   # Theil-Sen slope per period
    segments: list[SegmentOut] = []
    tier: Literal["confirmed", "probable", "uncertain"] = "uncertain"
    uncertain: bool = True
    on_residuals: bool = False                   # correlation measured after detrending (decision D-15)
    evidence: list[Evidence] = []


class TextFact(Strict):
    fact_key: str                                # e.g. 'text.s2.headcount'
    value: float
    unit: str | None = None
    scale: float = 1.0
    sentence: str
    section: str
    linked_to: str | None = None                 # 'table.column:sum' when it equals a table aggregate


class InferenceReport(Strict):
    schema_version: str = SCHEMA_VERSION
    source_sha256: str
    config_hash: str
    domain: str = "unknown"
    domain_confidence: float = Field(0.0, ge=0, le=1)
    reference_date: date | None = None
    table_quality: dict[str, TableQuality] = {}
    profiles: list[FieldProfile] = []
    rules: list[Rule] = []
    relationships: list[Relationship] = []
    patterns: list[PatternSpec] = []
    text_facts: list[TextFact] = []
    warnings: list[str] = []
    llm_used: bool = False
    # 'table.column' -> Sensitivity Class / importance proposed by the Annotator (M4 stricter-of merge).
    sensitivity_labels: dict[str, str] = {}
    column_importance: dict[str, str] = {}
    column_meanings: dict[str, str] = {}         # 'table.column' -> the Annotator's plain description
    column_roles: dict[str, str] = {}            # 'table.column' -> Annotator role (e.g. 'row_label')
    sensitivity_reasons: dict[str, str] = {}     # 'table.column' -> why the Annotator chose its label
    sensitivity_confidence: dict[str, float] = {}  # 'table.column' -> the Annotator's confidence
    key_columns: dict[str, list[str]] = {}       # Primary Keys per table (M3.1, recorded for generation)


# ---------------------------------------------------------------- privacy, validation
class PrivacyDecision(Strict):
    table_id: str
    column: str
    risk: SensitivityClass
    rule_label: SensitivityClass
    llm_label: SensitivityClass | None = None
    rule_strength: Literal["hard", "weak", "none"] = "none"   # hard = measured on the values
    relabel: bool = False                        # text values are replaced through the Label Map
    method_source: Literal["policy", "llm"] = "policy"   # who chose the method and noise
    keep_correlation: bool = True                # sample jointly with related columns
    criticality: int | None = Field(default=None, ge=0, le=10)   # LLM: how revealing (0-10)
    method: str
    noise: float | None = None
    importance: Literal["low", "medium", "high", "very_high"] = "medium"
    exempt: bool = False                         # entity name / branding (decision D-07)
    rationale: str


class PrivacyPlan(Strict):
    decisions: list[PrivacyDecision]
    tiny_tables: list[str] = []                  # tables handled by the jitter policy (decision D-11)
    preserved_values: list[str] = []             # entity name and address: never replaced anywhere
    label_pools: dict[str, list[str]] = {}       # 'table.column' -> fresh labels (LLM, checked in Python)
    label_maps: dict[str, dict[str, str]] = {}   # E-35: 'table.column' -> {source label: new label}
    kept_labels: dict[str, list[str]] = {}       # E-39: labels kept as standard vocabulary, per column
    avoid: list[str] = []                        # E-28: source proper nouns (avoid.json)
    locale: str | None = None                    # where the entity is (style hint for fake values only)
    warnings: list[str] = []                     # e.g. a column that will need numbered fallback labels


class Violation(Strict):
    check: str                                   # 'formula', 'foreign_key', 'value_distance', ...
    rule_id: str | None = None
    severity: Severity
    table_id: str | None = None
    column: str | None = None
    rows: list[int] = []
    message: str                                 # never contains raw sensitive values
    repairable: bool


class RepairRecord(Strict):
    attempt: int = Field(ge=1)
    action: str
    rule_ids: list[str] = []
    rows_affected: int = 0
    ok_after: bool


class UnmetTarget(Strict):
    pattern_id: str
    target: str
    achieved: str
    note: str = ""


class ValidationReport(Strict):
    ok: bool
    violations: list[Violation] = []
    unmet_targets: list[UnmetTarget] = []
    repairs: list[RepairRecord] = []

    @property
    def repairable(self) -> bool:
        errors = [v for v in self.violations if v.severity == Severity.ERROR]
        return bool(errors) and all(v.repairable for v in errors)


class PrivacyReport(Strict):
    ok: bool
    checks_run: list[str]
    violations: list[Violation] = []
    limitations: list[str] = ["Synthetic data is not automatically anonymous.",
                              "No formal differential privacy mechanism is implemented or claimed."]


class Fact(Strict):
    key: str                                     # placeholder key, e.g. 'revenue.total'
    value: float | str
    kind: Literal["number", "text", "word"]
    unit: str | None = None
    source: str                                  # 'orders.revenue:sum' or 'pattern:p3'
    band: str | None = None                      # qualitative label shown to writers (E-10)
    owner: str | None = None                     # the one section allowed to cite it first
    decimals: int | None = None                  # displayed decimals of the column it comes from
    scale: float | None = None                   # the column's multiplier to base units ('Rs cr' = 1e7), F-03


class ChartSpec(Strict):
    """A registered chart. Its data is read from the Display Table, never from anywhere else."""
    chart_id: str
    table_id: str
    label_column: str
    series_columns: list[str] = Field(min_length=1)
    title: str | None = None
    display: bool = True                 # a hidden table feeds a chart but is not printed (E-19)
    kind: Literal["line", "bar", "pie", "horizontal_bar", "donut"] = "line"
    unit: str | None = None              # shown on the axis/title, taken from the Field Profile
    has_data_labels: bool = False        # the source chart printed its values
    has_legend: bool = True
    # 'columns': one series per column, x = the label column's values.
    # 'rows' (statements): one series per line item in series_rows, x = series_columns (periods)
    orientation: Literal["columns", "rows"] = "columns"
    series_rows: list[str] = []
    # axis labels for a 'rows' chart whose period columns have no names ('Unnamed: 1'): the cells of
    # the table's own sub-header row ('Q3FY24', 'Q4FY24', 'Q1FY25'); empty = the column names
    x_labels: list[str] = []
    colors: list[str] = []               # v3.3 F-06: the SOURCE chart's own series colours, when it had one
    markers: bool = True                 # F-06: False when the source chart drew plain lines


class NarrativeSection(Strict):
    section_id: str
    title: str
    text: str


class NarrativeDoc(Strict):
    sections: list[NarrativeSection]
    word_count: int
    llm_used: bool = False
    blocks: dict[str, str] = {}          # block_id -> resolved text (M8.7)
    flags: list[str] = []                # fallbacks and dropped optional payload fields
    claims: int = 0                      # claims merged from the writers (M8.8)
    new_entities: int = 0
    conflicts: int = 0
    resolved: int = 0
    dropped: int = 0
    unknown_refs: int = 0
    repeats: dict = {}                   # v3.3 F-11: {found, fixed, dropped, flagged, flagged_sentences}


class RenderResult(Strict):
    html_path: str
    chart_paths: dict[str, str] = {}
    pdf_path: str | None = None
    pages: int = 0
    qa: dict[str, Any] = {}


class RunResult(Strict):
    run_id: str
    run_dir: str
    status: Literal["ok", "failed", "partial"]
    manifest_path: str
    pdf_path: str | None = None


class RunManifest(Strict):
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["ok", "failed", "partial"] = "partial"
    source_sha256: str
    blueprint_sha256: str | None = None
    plan_sha256: str | None = None
    seed: int
    config_hash: str
    config_snapshot: dict[str, Any]
    python_version: str
    packages: dict[str, str]
    llm: dict[str, Any]                          # {'enabled': bool, 'model': ..., 'prompt_versions': {...}}
    synthetic: bool = True                       # the ONLY record of synthetic status (E-32: never in the PDF)
    outputs: dict[str, str] = {}                 # artifact path -> sha256
    gates: dict[str, bool] = {}
    limitations: list[str] = []
