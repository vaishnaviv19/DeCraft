# tests/validation/test_repair.py
import pytest

from src.contracts import Severity, ValidationReport, Violation
from src.errors import HardConstraintError
from src.validation._repair import run_bounded_repair


class Counter:
    """validate() fails the first `fails` times; repair() advances the counter."""

    def __init__(self, fails: int, unrepairable: bool = False):
        self.fails, self.unrepairable, self.calls = fails, unrepairable, 0

    def validate(self, data):
        ok = self.calls >= self.fails
        violations = [] if ok else [Violation(check="x", severity=Severity.ERROR, message="bad",
                                              repairable=not self.unrepairable)]
        return ValidationReport(ok=ok, violations=violations)

    def repair(self, data, report, attempt):
        self.calls += 1
        return data, f"repair {attempt}", 1


def test_success_on_the_last_allowed_repair_is_not_an_error():
    counter = Counter(fails=2)
    data, report = run_bounded_repair(0, counter.validate, counter.repair, max_repairs=2)
    assert report.ok and len(report.repairs) == 2
    assert [r.ok_after for r in report.repairs] == [False, True]
    assert data == 0


def test_the_repair_limit_is_respected():
    counter = Counter(fails=5)
    with pytest.raises(HardConstraintError):
        run_bounded_repair(0, counter.validate, counter.repair, max_repairs=2)
    assert counter.calls == 2


def test_unrepairable_violations_are_not_repaired():
    counter = Counter(fails=1, unrepairable=True)
    with pytest.raises(HardConstraintError):
        run_bounded_repair(0, counter.validate, counter.repair, max_repairs=3)
    assert counter.calls == 0


def test_clean_data_needs_no_repair():
    counter = Counter(fails=0)
    _, report = run_bounded_repair(0, counter.validate, counter.repair, max_repairs=3)
    assert report.ok and report.repairs == [] and counter.calls == 0
