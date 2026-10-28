# tests/narrative/test_originality.py
from pathlib import Path
from src.core.originality import (is_generic_label, reused_names, source_proper_nouns,
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


def test_tone_profile_detects_person_and_length():
    t = tone_profile(['We grew our revenue. We opened new plants and our teams delivered.'])
    assert t['person'] == 'first_plural' and t['avg_sentence_words'] > 3

def test_no_fixed_document_type_in_prompts():
    root = Path(__file__).resolve().parents[2] / 'src'
    for f in ['narrative/_prompts.py', 'rendering/_prompts.py', 'style/_prompts.py', 'inference/_annotator.py']:
        low = (root / f).read_text(encoding='utf-8').lower()
        assert 'corporate' not in low and 'annual report' not in low and 'annual-report' not in low, f


def test_financial_line_items_stay_generic_in_the_model_off_path():
    """E-27 model-off path: a financial statement's line items are standard vocabulary, not names.
    Regression for the sample run that replaced Depreciation/Interest/Adjustments with 'Item D'."""
    for label in ['Depreciation', 'Interest', 'Adjustments', 'Minority Interest', 'Reported PAT',
                  'PAT att. to shareholders', 'Share of profit from Associate', 'EBITDA margins',
                  'Finance Costs', 'Trade Receivables', 'Earnings per share', 'Capacity utilisation',
                  'EBITDA margins (%)', 'YoY Growth %']:
        assert is_generic_label(label), label


def test_names_are_still_not_generic():
    for label in ['Mundra UMPP', 'Acme Power Ltd', 'Falcon Division', 'Priya Nair', 'Horizon Program',
                  'Coastal Thermal', 'Odisha Grid']:
        assert not is_generic_label(label), label


def test_market_data_rows_and_stub_periods_are_generic():
    for label in ['52 week high', 'Beta', 'Shareholding %', 'Promoters', "FII's", 'MFs/Insti',
                  'Price Performance', 'Absolute Return', 'No. of shares', '3 Month', '6 Month',
                  '1 Year', 'Margins (%)', 'Market Cap (cr)', 'Enterprise Value (cr)']:
        assert is_generic_label(label), label
