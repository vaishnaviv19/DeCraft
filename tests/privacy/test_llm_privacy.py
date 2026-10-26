# tests/privacy/test_llm_privacy.py
"""The LLM reads what each column means (any domain) and writes fake labels; Python keeps the hard
evidence, checks every fake value, and protects the entity name and address everywhere."""
import json

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.contracts import (FieldProfile, PrivacyDecision, PrivacyPlan, SensitivityClass, SourceDocument,
                           SourceTable, ValidationReport)
from src.contracts.dataset import CanonicalDataset
from src.core.distance import ensure_distance
from src.core.frames import table_frame
from src.generation import generate
from src.generation._tiny import jitter_cells
from src.inference import infer
from src.privacy import Relabeler, audit_privacy, plan_privacy
from src.privacy._agents import MethodAdvice, apply_advice, expand_pattern, find_entity
from src.privacy._labels import LabelColumn, fabricate_labels
from src.privacy._analysis import LabelMap, decide_risk, detect_evidence, detect_risk
from tests.conftest import FakeLLM

S = SensitivityClass
NAMES = ["Asha Rao", "Ben Ortiz", "Chen Wei", "Dana Fox", "Eli Hart", "Fay Lin"]
GENES = ["TP53", "BRCA1", "EGFR", "KRAS", "MYC", "TP53"]
LAB, ADDRESS = "Helix Labs", "12 River Road, Pune"


def assay_document(address=ADDRESS) -> SourceDocument:
    rows = [[f"S-{i + 1:03d}", NAMES[i], GENES[i], f"{1.5 + i * 0.7:.2f}", LAB, ADDRESS] for i in range(6)]
    table = SourceTable(table_id="assay", page=1, raw_cells=rows,
                        header=["Sample ID", "Patient Name", "Gene", "Expression", "Lab", "Lab Address"])
    return SourceDocument(document_sha256="bio12345", entity_name=LAB, entity_address=address,
                          tables=[table], text_blocks=[f"{LAB} Ltd, {ADDRESS} 411001. Assay report."])


def meaning(column, sensitivity, reason, confidence=0.9, role="category"):
    return {"column": column, "meaning": f"{column} of the assay", "role": role, "importance": "medium",
            "sensitivity": sensitivity, "sensitivity_reason": reason, "confidence": confidence}


ANNOTATION = json.dumps({"domain": "biology", "domain_confidence": 0.9, "columns": [
    meaning("Sample ID", "low_risk", "a laboratory sample code, not a person"),
    meaning("Patient Name", "direct_identifier", "names a real patient", 0.95),
    meaning("Gene", "low_risk", "public gene symbol"),
    meaning("Expression", "sensitive", "a genetic measurement about a patient", 0.8, "measurement"),
    meaning("Lab", "low_risk", "the company itself"),
    meaning("Lab Address", "low_risk", "the company's own address")]})
PN = "assay.Patient Name"


def pairs(col, mapping):
    return [{"col": col, "src": s, "new": n} for s, n in mapping.items()]


# Label Fabricator, first answer: one real name kept, one duplicate, one company name; genes kept as
# standard vocabulary; an invented column is ignored
FAKES = json.dumps({"labels": pairs(PN, {"Asha Rao": "Asha Rao", "Ben Ortiz": "Mira Sen", "Chen Wei": "Mira Sen",
                                         "Dana Fox": "Omar Haddad", "Eli Hart": "Helix Labs", "Fay Lin": "Lena Park"})
                    + pairs("assay.Gene", {g: g for g in dict.fromkeys(GENES)})
                    + pairs("assay.Invented Column", {"x": "y"})})
# the retry fixes exactly the three rejected labels
MORE = json.dumps({"labels": pairs(PN, {"Asha Rao": "Ravi Iyer", "Chen Wei": "Kiran Das", "Eli Hart": "Tom Becker"})})
ENTITY = json.dumps({"name": LAB, "address": ADDRESS, "location": "Pune, India"})
NO_ADVICE = json.dumps({"columns": []})
REPLIES = [ANNOTATION, ENTITY, NO_ADVICE, FAKES, MORE]


def llm_run(cfg, gateway_for, replies, doc=None):
    doc = doc or assay_document()
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    llm = FakeLLM(replies)
    gateway = gateway_for(llm)
    report = infer(doc, llm_cfg, gateway=gateway)
    plan = plan_privacy(doc, report, llm_cfg, gateway=gateway)
    llm_run.calls = llm.calls
    return doc, report, plan, llm_cfg


def by_column(plan):
    return {d.column: d for d in plan.decisions}


# ---------------------------------------------------------------- who decides the class
@pytest.mark.parametrize("header", ["Shipping", "Capacity", "Electricity cost", "Accounts payable",
                                    "Opinion score", "Spending"])
def test_keyword_fallback_matches_whole_words_only(header):
    assert detect_risk(header, pd.Series(["x", "y"]))[0] is S.LOW_RISK


@pytest.mark.parametrize("header,cls", [("Customer Name", S.DIRECT_IDENTIFIER),
                                        ("PinCode", S.QUASI_IDENTIFIER), ("Annual salary", S.SENSITIVE)])
def test_keyword_fallback_still_finds_real_header_words(header, cls):
    assert detect_risk(header, pd.Series(["x", "y"]))[0] is cls


def test_dates_written_as_text_are_not_phone_numbers():
    assert detect_risk("Period", pd.Series(["2024-03-31", "2024-06-30", "31/12/2024"]))[0] is S.LOW_RISK
    assert detect_evidence("Contact", pd.Series(["+91 98765 43210", "+44 20 7946 0958"]))[2] == "hard"


def test_trusted_llm_reasoning_replaces_a_keyword_guess():
    final, who = decide_risk(S.DIRECT_IDENTIFIER, "weak", S.LOW_RISK, "a lab sample code", 0.9)
    assert final is S.LOW_RISK and "LLM" in who


@pytest.mark.parametrize("reason,confidence", [("", 0.95), ("a lab sample code", 0.4), ("x", None)])
def test_unreasoned_or_unsure_llm_cannot_lower_risk(reason, confidence):
    assert decide_risk(S.DIRECT_IDENTIFIER, "weak", S.LOW_RISK, reason, confidence)[0] is S.DIRECT_IDENTIFIER


def test_only_a_very_confident_reasoned_llm_overrides_measured_evidence():
    final, who = decide_risk(S.DIRECT_IDENTIFIER, "hard", S.LOW_RISK, "a public support line", 0.95)
    assert final is S.LOW_RISK and "overrode measured evidence" in who
    assert decide_risk(S.DIRECT_IDENTIFIER, "hard", S.LOW_RISK, "a public support line", 0.85)[0] \
        is S.DIRECT_IDENTIFIER                                  # trusted, but below the override bar
    assert decide_risk(S.DIRECT_IDENTIFIER, "hard", S.LOW_RISK, "", 0.99)[0] is S.DIRECT_IDENTIFIER
    assert decide_risk(S.LOW_RISK, "none", S.SENSITIVE, "", None)[0] is S.SENSITIVE


# ---------------------------------------------------------------- a biology document end to end
def test_biology_columns_are_classified_by_meaning(cfg, gateway_for):
    _, _, plan, _ = llm_run(cfg, gateway_for, REPLIES)
    d = by_column(plan)
    assert d["Sample ID"].risk is S.LOW_RISK and "sample code" in d["Sample ID"].rationale
    assert d["Gene"].risk is S.LOW_RISK and not d["Gene"].relabel
    assert d["Patient Name"].risk is S.DIRECT_IDENTIFIER and d["Patient Name"].relabel
    assert d["Expression"].risk is S.SENSITIVE


def test_company_name_and_address_are_always_preserved(cfg, gateway_for):
    _, _, plan, _ = llm_run(cfg, gateway_for, REPLIES)
    d = by_column(plan)
    assert plan.preserved_values == [LAB, ADDRESS]
    assert d["Lab"].exempt and d["Lab Address"].exempt and d["Lab Address"].method == "preserve"


def test_fake_labels_are_checked_before_use(cfg, gateway_for):
    _, _, plan, _ = llm_run(cfg, gateway_for, REPLIES)
    assert plan.label_maps[PN] == {"Asha Rao": "Ravi Iyer", "Ben Ortiz": "Mira Sen", "Chen Wei": "Kiran Das",
                                   "Dana Fox": "Omar Haddad", "Eli Hart": "Tom Becker", "Fay Lin": "Lena Park"}
    assert plan.kept_labels["assay.Gene"] == list(dict.fromkeys(GENES))   # standard vocabulary stays
    assert "assay.Invented Column" not in plan.label_maps and plan.warnings == []
    second = json.loads(llm_run.calls[-1][1].split("<data>")[1].split("</data>")[0])
    assert [x["src"] for x in second["lab"]] == ["Asha Rao", "Chen Wei", "Eli Hart"]
    assert len(second["errors"]) == 3


def test_generated_data_uses_fake_names_and_keeps_the_company(cfg, gateway_for):
    doc, report, plan, llm_cfg = llm_run(cfg, gateway_for, REPLIES)
    frame = generate(doc, report, plan, llm_cfg, np.random.default_rng(3)).tables["assay"]
    names = set(frame["Patient Name"].dropna())
    assert names.isdisjoint(NAMES)
    assert names <= set(plan.label_maps[PN].values())                 # all believable, none numbered
    assert set(frame["Lab"]) == {LAB} and set(frame["Lab Address"]) == {ADDRESS}
    assert set(frame["Gene"]) <= set(GENES)                        # public gene symbols may stay


def test_address_comes_from_the_entity_agent_only_if_it_is_in_the_text(cfg, gateway_for):
    doc = assay_document(address=None)
    _, _, plan, _ = llm_run(cfg, gateway_for, REPLIES, doc=doc)
    assert ADDRESS in plan.preserved_values


def test_an_invented_address_is_not_trusted(gateway_for):
    gateway = gateway_for(FakeLLM([json.dumps({"name": "Helix Labs", "address": "99 Fake Street"})]))
    assert find_entity(f"{LAB} Ltd, {ADDRESS}", gateway, None) == ("Helix Labs", None, None)


def test_llm_off_still_protects_with_the_keyword_fallback(cfg):
    doc = assay_document()
    plan = plan_privacy(doc, infer(doc, cfg), cfg)
    d = by_column(plan)
    assert d["Patient Name"].risk is S.DIRECT_IDENTIFIER and d["Patient Name"].relabel
    assert d["Lab Address"].exempt and plan.label_pools == {}
    assert set(plan.label_maps[PN].values()) == {f"Patient Name {c}" for c in "ABCDEF"}   # spec fallback
    assert any("LLM off" in w for w in plan.warnings)


# ---------------------------------------------------------------- Label Map
def test_a_fake_label_is_never_any_real_label_and_never_hangs():
    real = ["North", "South", "East"]
    labels = LabelMap(lambda r: ["North", "South", "East"][r.integers(3)], np.random.default_rng(0),
                      forbidden=real, fallback_prefix="Region")
    fakes = [labels.get(x) for x in real + ["West"]]
    assert set(fakes).isdisjoint(real) and len(set(fakes)) == 4
    assert fakes[0] == labels.get("North")


def test_relabeler_keeps_one_fake_per_real_value_across_tables():
    plan = PrivacyPlan(decisions=[
        PrivacyDecision(table_id=t, column=c, risk=S.DIRECT_IDENTIFIER, rule_label=S.DIRECT_IDENTIFIER,
                        method="regenerate", relabel=True, rationale="x")
        for t, c in (("orders", "Customer"), ("invoices", "Client"))],
        preserved_values=[LAB], label_pools={"orders.Customer": ["Zed Cole", "Ann Park"],
                                             "invoices.Client": ["Ann Park", "Ivy Moss"]})
    r = Relabeler(plan, forbidden={"ann park"})
    a, b = r.get("orders", "Customer", "Ann"), r.get("invoices", "Client", "Ann")
    c = r.get("invoices", "Client", "Bob")
    assert a == b == "Zed Cole" and c == "Ivy Moss"            # 'Ann Park' is a real value: never used
    assert r.get("orders", "Customer", LAB) == LAB


# ---------------------------------------------------------------- distance after rounding
@pytest.mark.parametrize("new,old,decimals,expected", [(3.4, 3.0, 0, 4.0), (2.2, 2.1, 1, 2.3),
                                                       (0.07, 0.0, 0, 1.0), (130.0, 100.0, 0, 130.0)])
def test_distance_survives_display_rounding(new, old, decimals, expected):
    assert ensure_distance(new, old, 0.05, decimals) == expected


def test_tiny_table_jitter_never_prints_the_source_value():
    source = [3.0, 2.0, 7.0, 0.0, 1.0]
    for seed in range(20):
        out = jitter_cells(source, np.random.default_rng(seed), decimals=0)
        assert all(round(o) != s for o, s in zip(out, source))


def test_ambiguous_dates_are_not_guessed_when_reading_a_table():
    profile = [FieldProfile(table_id="t", column="D", physical_type="date", n=2, n_missing=0, n_unique=2)]
    ambiguous = SourceTable(table_id="t", page=1, header=["D"], raw_cells=[["03/04/2025"], ["05/06/2025"]])
    assert table_frame(ambiguous, profile)[0]["D"].isna().all()
    proven = SourceTable(table_id="t", page=1, header=["D"], raw_cells=[["25/12/2025"], ["03/04/2025"]])
    assert [d.date().isoformat() for d in table_frame(proven, profile)[0]["D"]] == ["2025-12-25", "2025-04-03"]


# ---------------------------------------------------------------- audit
def _audit(doc, report, cfg, tables, display=None, keys=None):
    data = CanonicalDataset(dataset_id="d", schema_version="2.4", seed=1, tables=tables, display=display or {},
                            key_columns=keys or {}, rules=[], facts={}, charts={}, lineage={},
                            validation=ValidationReport(ok=True), source_profiles=report.profiles)
    return audit_privacy(doc, data, plan_privacy(doc, report, cfg), cfg)


BIG = SourceTable(table_id="t_big", page=1, header=["Store", "Revenue"],
                  raw_cells=[[f"ST-{i}", f"{10 * (i + 1)}.00"] for i in range(6)])


def test_rounded_display_values_are_audited_too():
    cfg = load_config(llm_enabled=False)
    doc = SourceDocument(document_sha256="a", tables=[BIG])
    report = infer(doc, cfg)
    far = pd.DataFrame({"Store": [f"X-{i}" for i in range(6)], "Revenue": [15.0, 30, 45, 60, 75, 90]})
    printed = far.assign(Revenue=[10.0, 20, 30, 40, 50, 60])            # what the reader would see
    checks = {v.check for v in _audit(doc, report, cfg, {"t_big": far}, {"t_big": printed}).violations}
    assert checks == {"value_distance_displayed"}


def test_regenerated_keys_fall_back_to_row_position():
    cfg = load_config(llm_enabled=False)
    doc = SourceDocument(document_sha256="a", tables=[BIG])
    report = infer(doc, cfg)
    copied = pd.DataFrame({"Store": [f"X-{i}" for i in range(6)], "Revenue": [10.0, 20, 30, 40, 50, 60]})
    result = _audit(doc, report, cfg, {"t_big": copied}, keys={"t_big": ["Store"]})
    assert "value_distance" in {v.check for v in result.violations}      # before: compared nothing


def test_identifier_reuse_ignores_protected_and_total_labels(cfg, gateway_for):
    doc, report, plan, llm_cfg = llm_run(cfg, gateway_for, REPLIES)
    tables = {"assay": pd.DataFrame({"Sample ID": [f"Q-{i}" for i in range(6)],
                                     "Patient Name": ["Mira Sen"] * 5 + ["Total"],
                                     "Gene": [f"Gene {c}" for c in "ABCDEF"],   # LLM-off audit plan: genes fabricated
                                     "Expression": [9.0] * 6, "Lab": [LAB] * 6,
                                     "Lab Address": [ADDRESS] * 6})}
    clean = _audit(doc, report, llm_cfg, tables)
    assert "identifier_reuse" not in {v.check for v in clean.violations}
    tables["assay"].loc[0, "Patient Name"] = "Asha Rao"
    leaked = _audit(doc, report, llm_cfg, tables)
    assert "identifier_reuse" in {v.check for v in leaked.violations}


# ---------------------------------------------------------------- believable fakes: locale, rounds, patterns
def test_fake_values_are_asked_for_in_the_company_locale(cfg, gateway_for):
    _, _, plan, _ = llm_run(cfg, gateway_for, REPLIES)
    assert plan.locale == "Pune, India"
    fake_call = next(user for system, user in llm_run.calls if "text labels" in system)
    assert '"loc": "Pune, India"' in fake_call


def test_a_large_column_is_built_from_a_pattern_and_parts(gateway_for):
    reply = json.dumps({"labels": [], "patterns": [{"col": "crm.Customer", "pattern": "{first} {last}",
                                                    "parts": {"first": [f"F{i}a" for i in range(40)],
                                                              "last": [f"L{i}b" for i in range(40)]}}]})
    llm = FakeLLM([reply])
    column = LabelColumn("crm.Customer", "Customer", "client name", [f"Real {i}" for i in range(900)],
                         "direct_identifier", must_replace=True)
    made = fabricate_labels([column], gateway_for(llm), {"f1a l1b"}, set(), set())
    values = made.pools["crm.Customer"]
    assert len(values) == 900 and len(set(values)) == 900 and made.warnings == []
    assert "F1a L1b" not in values                              # a real value is never produced
    assert '"pat"' in llm.calls[0][1]


def test_a_pattern_with_unknown_slots_produces_nothing():
    rng = np.random.default_rng(0)
    assert expand_pattern("{first} {ghost}", {"first": ["A"]}, 5, rng, set(), set()) == []
    assert len(expand_pattern("LAB-{digits}", {}, 5, rng, set(), set())) == 5


def test_a_shortfall_gets_the_spec_fallback_and_a_warning(gateway_for):
    llm = FakeLLM([json.dumps({"labels": pairs("t.Name", {"Ann": "Ann Lee"})}), json.dumps({"labels": []})])
    column = LabelColumn("t.Name", "Name", "", ["Ann", "Bob", "Cy"], must_replace=True)
    made = fabricate_labels([column], gateway_for(llm), set(), set(), set())
    assert made.maps["t.Name"] == {"Ann": "Ann Lee", "Bob": "Name A", "Cy": "Name B"}
    assert made.fallback["t.Name"] == ["Bob", "Cy"] and len(made.warnings) == 2


# ---------------------------------------------------------------- LLM-chosen privacy methods (old PrivacyAgent)
def _decision(method="fitted_distribution", noise=0.12, exempt=False):
    return PrivacyDecision(table_id="t", column="c", risk=S.SENSITIVE, rule_label=S.SENSITIVE,
                           method=method, noise=noise, exempt=exempt, rationale="policy")


def _advice(method, confidence, noise=None, reason="because"):
    return MethodAdvice(column="c", method=method, noise=noise, confidence=confidence, reason=reason,
                        keep_correlation=False, criticality=8)


def test_more_protection_needs_ordinary_trust():
    d = apply_advice(_decision(), _advice("regenerate", 0.75), False, 0.70, 0.90)
    assert (d.method, d.method_source, d.keep_correlation, d.criticality) == ("regenerate", "llm", False, 8)


def test_less_protection_needs_the_override_bar():
    assert apply_advice(_decision(), _advice("category_resample", 0.85), False, 0.70, 0.90).method == "fitted_distribution"
    assert apply_advice(_decision(), _advice("category_resample", 0.95), False, 0.70, 0.90).method == "category_resample"
    assert apply_advice(_decision(), _advice("fitted_distribution", 0.8, noise=0.02), False, 0.7, 0.9).noise == 0.12


def test_no_confidence_lets_a_column_keep_its_source_values():
    """E-34 (task 6): only the organisation's own columns are preserved."""
    assert apply_advice(_decision(), _advice("preserve", 0.99), False, 0.70, 0.90).method == "fitted_distribution"
    assert apply_advice(_decision(), _advice("preserve", 0.99), True, 0.70, 0.90).method == "fitted_distribution"


@pytest.mark.parametrize("decision,advice", [
    (_decision(exempt=True), _advice("regenerate", 0.99)),           # the company name stays
    (_decision(method="derived", noise=0.0), _advice("regenerate", 0.99)),   # rule outputs stay computed
    (_decision(), _advice("regenerate", 0.99, reason="")),            # no reason, no change
    (_decision(), _advice("teleport", 0.99)),                         # unknown method
])
def test_advice_that_is_never_applied(decision, advice):
    assert apply_advice(decision, advice, False, 0.70, 0.90) == decision


def test_text_columns_only_take_text_methods_and_regenerate_relabels():
    assert apply_advice(_decision(), _advice("trend_preserving", 0.99), True, 0.7, 0.9).method == "fitted_distribution"
    assert apply_advice(_decision(), _advice("regenerate", 0.8), True, 0.7, 0.9).relabel


def test_llm_method_advice_reaches_the_plan(cfg, gateway_for):
    advice = json.dumps({"columns": [{"column": "Expression", "method": "regenerate", "noise": None,
                                      "keep_correlation": False, "criticality": 9,
                                      "reason": "genetic results reveal a diagnosis", "confidence": 0.8}]})
    _, _, plan, _ = llm_run(cfg, gateway_for, [ANNOTATION, ENTITY, advice, FAKES, MORE])
    d = by_column(plan)["Expression"]
    assert (d.method, d.method_source, d.criticality) == ("regenerate", "llm", 9)
    assert "diagnosis" in d.rationale
