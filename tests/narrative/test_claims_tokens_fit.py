# tests/narrative/test_claims_tokens_fit.py
from src.core.periods import Period, format_like, parse_period, shift_period
from src.narrative._claims import Claim, Entity, check_resolution, merge_claims
from src.narrative._tokens import illegal_digits
from src.rendering._fit import Box, fits, word_budget

BIBLE = {'E1': Entity('E1', 'product', 'Nimbus Platform', {'year': 'FY24'}),
         'E2': Entity('E2', 'person_role', 'Chief Executive Officer', {'name': 'Asha Raman'})}


def test_agreeing_claims_produce_no_conflict():
    _, conflicts, unknown = merge_claims(BIBLE, [Claim('S1', 0, 'E1', 'year', 'FY 24')], [])
    assert conflicts == [] and unknown == []


def test_contradiction_with_bible_is_a_conflict_listing_both_values():
    _, conflicts, _ = merge_claims(BIBLE, [Claim('S3', 1, 'E1', 'year', 'FY25')], [])
    assert len(conflicts) == 1 and conflicts[0].values == ('FY24', 'FY25')


def test_two_writers_disagree_on_new_attr():
    cs = [Claim('S1', 0, 'E1', 'location', 'Pune'), Claim('S2', 0, 'E1', 'location', 'Chennai')]
    _, conflicts, _ = merge_claims(BIBLE, cs, [])
    assert conflicts[0].attr == 'location' and set(conflicts[0].values) == {'Pune', 'Chennai'}


def test_new_attr_without_disagreement_is_added():
    bible, conflicts, _ = merge_claims(BIBLE, [Claim('S1', 0, 'E1', 'location', 'Pune')], [])
    assert not conflicts and bible['E1'].attrs['location'] == 'Pune' and 'location' not in BIBLE['E1'].attrs


def test_new_entity_with_existing_name_is_merged_not_duplicated():
    ne = Entity('N:S4:0', 'product', 'nimbus  platform', {}, 'S4')
    bible, conflicts, unknown = merge_claims(BIBLE, [Claim('S4', 0, 'N:S4:0', 'year', 'FY23')], [ne])
    assert 'N:S4:0' not in bible and conflicts and conflicts[0].entity == 'E1'


def test_unknown_reference_is_reported():
    assert merge_claims(BIBLE, [Claim('S1', 0, 'E9', 'year', 'FY24')], [])[2]


def test_reconciler_may_only_choose_existing_values():
    _, [c], _ = merge_claims(BIBLE, [Claim('S3', 1, 'E1', 'year', 'FY25')], [])
    assert check_resolution(c, 'fy25') and not check_resolution(c, 'FY26')


def test_period_tokens_allowed_only_if_in_the_generated_periods():
    allowed = {'FY2024', 'FY2025', 'Q3 FY2025', '2025'}
    assert illegal_digits('Launched in FY25 and scaled in Q3 FY25 to {{rev.total|bn:1}} billion.', allowed) == []
    assert illegal_digits('Growth in FY27 was strong.', allowed) == ['FY27']
    assert illegal_digits('We opened 3 plants in 2025.', allowed) == ['3']
    assert illegal_digits('Revenue rose 12% in 2025.', allowed) == ['12%']


def test_period_shift_keeps_label_style():
    p = parse_period('FY24')
    assert format_like('FY24', shift_period(p, 1)) == 'FY25'
    assert format_like('Q3 2023', shift_period(parse_period('Q3 2023'), 1)) == 'Q3 2024'


def test_text_fit_and_budget():
    box = Box(width_pt=100, height_pt=36, font_size=10, line_height=1.2)   # 3 lines
    w = lambda s: 6.0 * len(s)                                             # fake 6 pt per char
    assert fits('one two three four five six', box, w)[0]
    assert not fits(' '.join(['word'] * 40), box, w)[0]
    assert word_budget(100) == (85, 115)
