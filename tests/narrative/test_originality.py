# tests/narrative/test_originality.py
from pathlib import Path
from src.narrative._originality import (check_label_map, is_generic_label, reused_names, source_proper_nouns,
                                        tone_profile)

SRC = ("Acme Industries reported strong growth. The Falcon X200 launch in Pune was led by Priya Nair. "
       "In North America, the Horizon Program expanded.")


def test_proper_nouns_exclude_organisation_and_generic_words():
    names = source_proper_nouns(SRC, keep={'Acme Industries', 'Acme'})
    assert {'Falcon X200', 'Pune', 'Priya Nair', 'Horizon Program'} <= names
    assert not any('Acme' in n for n in names) and 'North America' not in names


def test_generated_text_reusing_source_names_is_caught():
    names = source_proper_nouns(SRC, keep={'Acme Industries'})
    assert reused_names('Acme launched the Falcon X200 in Chennai.', names) == ['Falcon X200']
    assert reused_names(['Nimbus Platform', 'Project Meridian'], names) == []


def test_company_specific_labels_must_be_replaced_generic_may_stay():
    assert is_generic_label('North America') and is_generic_label('Revenue') and is_generic_label('FY24')
    assert check_label_map([('Falcon Division', 'Falcon Division')])
    assert check_label_map([('Europe', 'Europe'), ('Falcon Division', 'Orion Systems')]) == []
    assert check_label_map([('A Unit', 'Orion'), ('B Unit', 'orion')])          # duplicate replacement


def test_tone_profile_detects_person_and_length():
    t = tone_profile(['We grew our revenue. We opened new plants and our teams delivered.'])
    assert t['person'] == 'first_plural' and t['avg_sentence_words'] > 3


def test_no_fixed_document_type_in_prompts():
    root = Path(__file__).resolve().parents[2] / 'src'
    for f in ['narrative/_prompts.py', 'rendering/_prompts.py', 'style/_prompts.py', 'inference/_annotator.py']:
        low = (root / f).read_text(encoding='utf-8').lower()
        assert 'corporate' not in low and 'annual report' not in low and 'annual-report' not in low, f
