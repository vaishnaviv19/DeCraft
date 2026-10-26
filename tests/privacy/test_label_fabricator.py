# tests/privacy/test_label_fabricator.py  (E-35 / E-39: one agent for every text label, Python guards)
import json

from src.core.originality import name_words
from src.privacy._labels import LABELS_PROMPT, LabelColumn, fabricate_labels
from tests.conftest import FakeLLM

PROSE = ("During the quarter the plant at Mundra ran at full load while demand in Odisha grew steadily "
         "and the market for power stayed firm.")
NAMES = name_words(PROSE, "Acme Power Ltd")


def reply(*pairs):
    return json.dumps({"labels": [{"col": c, "src": s, "new": n} for c, s, n in pairs]})


def seg(*labels):
    return LabelColumn("seg.Segment", "Segment", "business segment", list(labels))


def test_name_words_find_places_and_the_organisation_not_vocabulary():
    assert {"mundra", "odisha", "acme"} <= NAMES
    assert not {"market", "power", "quarter", "plant"} & NAMES       # written in lowercase somewhere


def test_standard_vocabulary_may_stay_but_a_kept_name_is_forced_out(gateway_for):
    llm = FakeLLM([reply(("seg.Segment", "Depreciation", "Depreciation"), ("seg.Segment", "- Mundra UMPP", "- Mundra UMPP")),
                   reply(("seg.Segment", "- Mundra UMPP", "- Coastal Thermal"))])
    made = fabricate_labels([seg("Revenue", "Depreciation", "- Mundra UMPP")], gateway_for(llm), set(), set(), NAMES)
    assert made.kept["seg.Segment"] == ["Revenue", "Depreciation"]
    assert made.maps["seg.Segment"] == {"- Mundra UMPP": "- Coastal Thermal"}
    asked = json.loads(llm.calls[0][1].split("<data>")[1].split("</data>")[0])["lab"]
    assert "Revenue" not in [x["src"] for x in asked]               # generic vocabulary needs no call
    assert "mundra" in json.loads(llm.calls[1][1].split("<data>")[1].split("</data>")[0])["errors"][0]


def test_a_replacement_may_not_reuse_a_source_name_or_value(gateway_for):
    llm = FakeLLM([reply(("seg.Segment", "Grid East", "Odisha Grid"), ("seg.Segment", "Grid West", "Grid East")),
                   reply(("seg.Segment", "Grid East", "Lakeside Grid"), ("seg.Segment", "Grid West", "Hillcrest Grid"))])
    forbidden = {"grid east", "grid west"}
    made = fabricate_labels([seg("Grid East", "Grid West")], gateway_for(llm), forbidden, set(), NAMES)
    assert made.maps["seg.Segment"] == {"Grid East": "Lakeside Grid", "Grid West": "Hillcrest Grid"}


def test_avoid_list_names_are_rejected(gateway_for):
    llm = FakeLLM([reply(("seg.Segment", "Unit One", "Horizon Program")), reply(("seg.Segment", "Unit One", "Unit Delta"))])
    made = fabricate_labels([seg("Unit One")], gateway_for(llm), set(), {"Horizon Program"}, set())
    assert made.maps["seg.Segment"] == {"Unit One": "Unit Delta"}


def test_one_label_in_two_tables_gets_one_fake(gateway_for):
    llm = FakeLLM([reply(("seg.Segment", "Jojobera", "Ridgeview"))])
    other = LabelColumn("plants.Plant", "Plant", "plant", ["Jojobera"])
    made = fabricate_labels([seg("Jojobera"), other], gateway_for(llm), set(), set(), set())
    assert made.maps["seg.Segment"]["Jojobera"] == made.maps["plants.Plant"]["Jojobera"] == "Ridgeview"
    assert len(llm.calls) == 1


def test_llm_off_keeps_only_the_spec_list_and_fallbacks_keep_units():
    """Updated for E-27: 'Tax Rate (%)' is a measure of the field, so the deterministic path keeps it
    instead of numbering it. The fallback still keeps a '- ' prefix and a unit suffix."""
    made = fabricate_labels([seg("Revenue", "Tax Rate (%)", "- Mundra UMPP (cr)")], None, set(), set(), NAMES)
    assert made.kept["seg.Segment"] == ["Revenue", "Tax Rate (%)"]
    assert made.maps["seg.Segment"] == {"- Mundra UMPP (cr)": "- Segment A (cr)"}


def test_llm_off_keeps_financial_line_items_instead_of_numbering_them():
    """Regression: the sample run replaced Depreciation/Interest/Adjustments with 'Item D' ... 'Item N'.
    They are standard line items of the field, so the deterministic path must keep them (E-27)."""
    made = fabricate_labels([seg("Depreciation", "Interest", "Adjustments", "Minority Interest",
                                 "- Mundra UMPP")], None, set(), set(), NAMES)
    assert made.kept["seg.Segment"] == ["Depreciation", "Interest", "Adjustments", "Minority Interest"]
    assert made.maps["seg.Segment"] == {"- Mundra UMPP": "- Segment A"}


def test_the_entity_name_is_never_sent_or_replaced(gateway_for):
    llm = FakeLLM([reply(("seg.Segment", "Unit One", "Unit Delta"))])
    made = fabricate_labels([seg("Acme Power Ltd", "Unit One")], gateway_for(llm), set(), set(), set(),
                            protected=["Acme Power Ltd"])
    assert "Acme Power Ltd" not in llm.calls[0][1] and "Acme Power Ltd" not in made.maps["seg.Segment"]


def test_prompt_names_no_document_type():
    text = LABELS_PROMPT.system.lower()
    assert "annual report" not in text and "corporate" not in text and "<data>" in text


# ---------------------------------------------------------------- stacked tables: numbers stored as text
def test_numbers_and_periods_in_a_text_column_are_not_labels():
    from src.privacy import is_word_label
    assert is_word_label("Market Cap (cr)") and is_word_label("3 Month")
    assert not any(is_word_label(v) for v in ["46.9", "21.9%", "Rs. 277", "Q3FY23", "FY24E", "-"])


def test_a_fabricated_column_swaps_only_its_pairs():
    from src.contracts import PrivacyDecision, PrivacyPlan, SensitivityClass
    from src.privacy import Relabeler
    d = PrivacyDecision(table_id="t", column="c", risk=SensitivityClass.LOW_RISK,
                        rule_label=SensitivityClass.LOW_RISK, method="category_resample", rationale="x")
    plan = PrivacyPlan(decisions=[d], label_maps={"t.c": {"- Mundra UMPP": "- Coastal Thermal"}})
    r = Relabeler(plan, forbidden={"46.9"})
    assert r.wants("t", "c")
    assert r.get("t", "c", "- Mundra UMPP") == "- Coastal Thermal"
    assert r.get("t", "c", "46.9") == "46.9" and r.get("t", "c", "Revenue") == "Revenue"


# ---------------------------------------------------------------- E-41: real tickers stay (branding)
def test_listing_codes_are_kept_like_the_company_name():
    import numpy as np
    from src.config import load_config
    from src.contracts import SourceDocument, SourceTable
    from src.generation import generate
    from src.inference import infer
    from src.privacy import listing_codes, plan_privacy
    cfg = load_config(llm_enabled=False)
    table = SourceTable(table_id="t1", page=1, header=["BSE CODE: 500400", "NSE CODE: TATAPOWER"],
                        raw_cells=[["Bloomberg CODE: TPWR:IN", "SENSEX: 67,127"]])
    doc = SourceDocument(document_sha256="tick1234", entity_name="Tata Power Company Limited", tables=[table],
                         text_blocks=["Tata Power Company Limited results update for the quarter."])
    assert listing_codes(doc) == ["BSE CODE: 500400", "NSE CODE: TATAPOWER", "Bloomberg CODE: TPWR:IN"]
    report = infer(doc, cfg)
    plan = plan_privacy(doc, report, cfg)
    assert "Bloomberg CODE: TPWR:IN" in plan.preserved_values
    assert not any("Bloomberg CODE: TPWR:IN" in m for m in plan.label_maps.values())
    frame = generate(doc, report, plan, cfg, np.random.default_rng(1)).tables["t1"]
    assert frame.iloc[0, 0] == "Bloomberg CODE: TPWR:IN"
    assert frame.iloc[0, 1] != "SENSEX: 67,127" and frame.iloc[0, 1].startswith("SENSEX: ")  # an index level is data


def test_numbers_inside_a_replacement_label_must_move_like_data(gateway_for):
    llm = FakeLLM([reply(("idx.Index", "SENSEX: 67,127", "SENSEX: 67,207")),
                   reply(("idx.Index", "SENSEX: 67,127", "SENSEX: 58,940"))])
    made = fabricate_labels([LabelColumn("idx.Index", "Index", "market index level", ["SENSEX: 67,127"])],
                            gateway_for(llm), set(), set(), set())
    assert made.maps["idx.Index"] == {"SENSEX: 67,127": "SENSEX: 58,940"}
    errors = json.loads(llm.calls[1][1].split("<data>")[1].split("</data>")[0])["errors"]
    assert "67,207" in errors[0] and "5%" in errors[0]


def test_a_key_cell_keeps_the_fabricated_replacement():
    import numpy as np
    from src.config import load_config
    from src.contracts import SourceDocument, SourceTable
    from src.generation import generate
    from src.inference import infer
    from src.privacy import plan_privacy
    cfg = load_config(llm_enabled=False)
    table = SourceTable(table_id="t1", page=1, header=["BSE CODE: 500400", "NSE CODE: TATAPOWER"],
                        raw_cells=[["Bloomberg CODE: TPWR:IN", "SENSEX: 67,127"]])
    doc = SourceDocument(document_sha256="tick5678", entity_name="Tata Power Company Limited", tables=[table],
                         text_blocks=["Tata Power Company Limited results update for the quarter."])
    report = infer(doc, cfg)
    plan = plan_privacy(doc, report, cfg)
    plan = plan.model_copy(update={"label_maps": {"t1.NSE CODE: TATAPOWER": {"SENSEX: 67,127": "SENSEX: 73,842"}}})
    frame = generate(doc, report, plan, cfg, np.random.default_rng(1)).tables["t1"]
    assert frame.iloc[0, 1] == "SENSEX: 73,842"
