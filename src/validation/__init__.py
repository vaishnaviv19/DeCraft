# src/validation/__init__.py  -- PUBLIC INTERFACE of the validation module (deep module)
"""Pure validation of a Canonical Dataset; never modifies it.

Stages: schema and numbers; Primary Keys; Foreign Keys; formulas; ordering; Total Rows and
aggregates; privacy distance and range checks when the Source Document is given; utility warnings.
"""
from __future__ import annotations

from src.config import Config
from src.contracts import Severity, SourceDocument, ValidationReport
from src.contracts.dataset import CanonicalDataset
from src.validation import _checks
from src.validation._repair import repair_dataset, run_bounded_repair

__all__ = ["validate", "run_bounded_repair", "repair_dataset"]


def validate(data: CanonicalDataset, report, cfg: Config,
             source: SourceDocument | None = None) -> ValidationReport:
    """Return a Validation Report. `source` enables Source Distance and utility checks."""
    violations = []
    frames = _checks.source_frames(source, report) if source is not None else None
    violations += _checks.check_numbers(data, frames)
    violations += _checks.check_keys(data)
    violations += _checks.check_foreign_keys(data, report)
    violations += _checks.check_formulas(data, report, cfg)
    violations += _checks.check_row_formulas(data, report, cfg, frames)
    violations += _checks.check_ordering(data, report)
    violations += _checks.check_totals(data, report)
    violations += _checks.check_aggregates(data, report)
    violations += _checks.check_patterns(data, report, cfg, frames)
    if frames is not None:
        violations += _checks.check_source_distance(data, report, cfg, frames)
        violations += _checks.check_dcr(data, report, cfg, frames)          # v3.3 F-02
        violations += _checks.check_ranges(data, report, frames)
        violations += _checks.check_utility(data, report, cfg, frames)
    return ValidationReport(
        ok=not any(v.severity is Severity.ERROR for v in violations),
        violations=violations)
