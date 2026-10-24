# tests/contracts/test_contracts.py
import pytest
from pydantic import ValidationError
from src.contracts import (ColumnRef, FieldProfile, InferenceReport, Rule, RuleKind, RuleTier,
                           DecisionStatus, Enforcement)


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        ColumnRef(table_id='t', column='c', extra=1)


def test_node_is_qualified():
    assert ColumnRef(table_id='orders', column='Total').node == 'orders.Total'


def test_profile_counts_must_be_consistent():
    with pytest.raises(ValidationError):
        FieldProfile(table_id='t', column='c', physical_type='float', n=5, n_missing=2, n_unique=4)


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Rule(rule_id='r', kind=RuleKind.FORMULA, table_id='t', columns=['a'], tier=RuleTier.SEMANTIC,
             status=DecisionStatus.ACCEPTED, enforcement=Enforcement.HARD, llm_confidence=1.4, reason='x')


def test_inference_report_round_trips_json():
    r = InferenceReport(source_sha256='abc', config_hash='def', warnings=['w'])
    assert InferenceReport.model_validate_json(r.model_dump_json()) == r
