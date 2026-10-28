# tests/core/test_label_names.py  -- E-52: which words of a label are names (model off)
from src.core.originality import label_names, lower_words

TEXT = ("The Odisha business stabilised. The company recorded revenue growth; capacity in the renewable "
        "energy segment and coal operations improved, other investments and cash rose; the target price "
        "is based on regulated equity.")
LOWER = lower_words(TEXT)
NAMES = {"odisha"}
ORG = "Tata Power Company Limited"


def names(label, statement=False):
    return label_names(label, LOWER, NAMES, ORG, statement)


def test_places_and_unknown_proper_words_are_names():
    assert names("- Mumbai Operations") == ["Mumbai"]          # a known place
    assert names("- Odisha Discoms")[0] == "Odisha"             # capitalised in the prose, never lower
    assert names("- Jojobera") == ["Jojobera"]                  # no English form, never written lower


def test_standard_wording_stays():
    for label in ("- Coal Operations", "- Renewables", "- Other investments", "- Cash", "Target Price",
                  "P/BV", "EV/EBITDA", "Upside is between 10%-15%", "Regulated Equity"):
        assert names(label) == [], label


def test_statement_line_items_are_standard_vocabulary():
    for label in ("Reserves & Surplus", "Accounts Receivable", "Def. Tax (Net)", "% change",
                  "PAT att. to common shareholders"):
        assert names(label, statement=True) == [], label


def test_the_organisation_is_branding_not_a_name_to_replace():
    assert names("- Tata Power Trading") == []
