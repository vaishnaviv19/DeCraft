# tests/contracts/test_report_contracts.py
import pytest
from pydantic import ValidationError
from src.contracts.report import CriticOut, WriterOut, StoryBible


def test_writer_output_parses_minimal_json():
    w = WriterOut.model_validate({'blocks': [{'id': 'p2b3', 'text': 'Revenue {{rev.trend|word}}.'}]})
    assert w.claims == [] and w.new == []


def test_critic_param_outside_allowlist_rejected():
    with pytest.raises(ValidationError):
        CriticOut.model_validate({'score': 7, 'issues': [{'problem': 'x', 'param': 'change_numbers'}]})


def test_bible_kind_must_be_known():
    with pytest.raises(ValidationError):
        StoryBible.model_validate({'entities': [{'id': 'E1', 'kind': 'celebrity', 'name': 'x'}]})
