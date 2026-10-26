# tests/generation/test_prepare.py
import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.contracts import SourceDocument, SourceTable
from src.contracts.report import RecoveredChart, RecoveredSeries
from src.generation import generate, prepare_for_report
from src.generation._prepare import _growth_band, _share_band
from src.inference import infer
from src.privacy import plan_privacy
from src.style import build_templates, extract_style

CFG = load_config(llm_enabled=False)


def run(tables, text="Acme Industries reported growth."):
    doc = SourceDocument(document_sha256="prep12345", tables=tables, text_blocks=[text])
    report = infer(doc, CFG)
    data = generate(doc, report, plan_privacy(doc, report, CFG), CFG,
                    np.random.default_rng(CFG.seed))
    return doc, report, data


def prepare(doc, report, data, recovered=(), templates=()):
    return prepare_for_report(data, report, [], list(recovered), list(templates), CFG,
                              np.random.default_rng(CFG.seed), gateway=None, doc=doc)


def periods_table():
    return SourceTable(table_id="t_periods", page=1, header=["Year", "Sales"],
                       raw_cells=[[f"FY{21 + i}", str(100 + 10 * i)] for i in range(5)])


def test_periods_shift_by_one_year_and_allowed_forms_are_recorded(tmp_path):
    document, report, data = run([periods_table()])
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg,
                                  np.random.default_rng(cfg.seed), gateway=None, doc=document)
    assert list(prepared.tables["t_periods"]["Year"]) == ["FY22", "FY23", "FY24", "FY25", "FY26"]
    assert {"FY25", "FY2025"} <= set(prepared.allowed_periods)


def test_company_specific_labels_are_replaced_consistently_and_generic_kept(tmp_path):
    first = SourceTable(table_id="t1", page=1, header=["Division", "Value"],
                        raw_cells=[["Falcon Division", "10"], ["North", "20"], ["Aurora Unit", "30"]])
    second = SourceTable(table_id="t2", page=2, header=["Division", "Value"],
                         raw_cells=[["Falcon Division", "5"], ["North", "7"]])
    document, report, data = run(
        [first, second], text="Acme Industries reported growth at Falcon Division and Aurora Unit.")
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg,
                                  np.random.default_rng(cfg.seed), gateway=None, doc=document,
                                  out_dir=tmp_path / "prep")
    values = set(prepared.tables["t1"]["Division"]) | set(prepared.tables["t2"]["Division"])
    assert "Falcon Division" not in values and "Aurora Unit" not in values
    assert "North" in values
    avoid = (tmp_path / "prep" / "avoid.json").read_text(encoding="utf-8")
    assert "Falcon Division" in avoid and "Aurora Unit" in avoid


def test_row_counts_come_back_into_the_allowed_band(tmp_path):
    rows = [[f"Item {i}", str(10 * (i + 1))] for i in range(10)]
    rows.append(["Total", str(sum(10 * (i + 1) for i in range(10)))])
    table = SourceTable(table_id="t_rows", page=1, header=["Item", "Value"], raw_cells=rows)
    document, report, data = run([table])
    frame = data.tables["t_rows"]
    extra = frame.iloc[:5].copy()                # 15 leaves against a source of 10
    data.tables["t_rows"] = pd.concat([frame, extra], ignore_index=True)
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg,
                                  np.random.default_rng(cfg.seed), gateway=None, doc=document)
    assert 8 <= len(prepared.tables["t_rows"]) - 1 <= 12
    total = prepared.tables["t_rows"].iloc[-1]["Value"]
    leaves = prepared.tables["t_rows"].iloc[:-1]["Value"].sum()
    assert total == pytest.approx(leaves)


def test_a_recovered_chart_becomes_a_hidden_table(tmp_path):
    rows = [[f"Item {i}", str(10 + i)] for i in range(8)]
    table = SourceTable(table_id="t_small", page=1, header=["Item", "Value"], raw_cells=rows)
    document, report, data = run([table])
    chart = RecoveredChart(figure_id="p1b02", categories=["A", "B", "C"],
                           series=[RecoveredSeries(name="S1", values=[1, 2, 3])], confidence=0.8)
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [chart], [], cfg,
                                  np.random.default_rng(cfg.seed), gateway=None, doc=document)
    assert "rc_p1b02" in prepared.tables
    spec = prepared.charts["chart_p1b02"]
    assert spec.display is False and spec.label_column == "Category"


def test_facts_have_a_band_and_exactly_one_owner(tmp_path):
    document, report, data = run([periods_table()])
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg,
                                  np.random.default_rng(cfg.seed), gateway=None, doc=document)
    assert prepared.facts and all(fact.owner == "S1" for fact in prepared.facts.values())
    shares = [fact for key, fact in prepared.facts.items() if key.endswith("largest_share")]
    assert shares and shares[0].band in {"dominant", "major", "minor"}
    assert prepared.validation.ok


def test_band_thresholds():
    assert _growth_band(12) == "strong increase"
    assert _growth_band(5) == "increase"
    assert _growth_band(0) == "flat"
    assert _growth_band(-5) == "decline"
    assert _growth_band(-12) == "sharp decline"
    assert _share_band(60) == "dominant" and _share_band(30) == "major" and _share_band(10) == "minor"


# ---------------------------------------------------------------- E-35 / E-39: one Label Fabricator
def _statement():
    return SourceTable(table_id="t_pl", page=1, header=["Particulars", "FY23", "FY24"],
                       raw_cells=[["Depreciation", "10", "12"], ["- Mundra UMPP", "40", "44"],
                                  ["Revenue", "100", "110"]])


def test_labels_the_privacy_plan_kept_or_made_are_not_replaced_again(tmp_path):
    from src.contracts import PrivacyPlan
    doc, report, data = run([_statement()], text="Our plant at Mundra ran at full load during the quarter.")
    frame = data.tables["t_pl"]
    frame["Particulars"] = ["Depreciation", "- Aravalli UMPP", "Revenue"]      # as the privacy plan left it
    plan = PrivacyPlan(decisions=[], kept_labels={"t_pl.Particulars": ["Depreciation", "Revenue"]},
                       label_maps={"t_pl.Particulars": {"- Mundra UMPP": "- Aravalli UMPP"}})
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg, np.random.default_rng(1), gateway=None,
                                  doc=doc, privacy_plan=plan)
    assert list(prepared.tables["t_pl"]["Particulars"]) == ["Depreciation", "- Aravalli UMPP", "Revenue"]


def test_a_source_label_nobody_decided_goes_through_the_same_fabricator(tmp_path):
    from src.contracts import PrivacyPlan
    doc, report, data = run([_statement()], text="Our plant at Mundra ran at full load during the quarter.")
    data.tables["t_pl"]["Particulars"] = ["Depreciation", "- Mundra UMPP", "Revenue"]   # a leaked source name
    plan = PrivacyPlan(decisions=[], kept_labels={"t_pl.Particulars": ["Depreciation", "Revenue"]})
    cfg = CFG.model_copy(update={"artifacts_dir": str(tmp_path)})
    prepared = prepare_for_report(data, report, [], [], [], cfg, np.random.default_rng(1), gateway=None,
                                  doc=doc, privacy_plan=plan)
    labels = list(prepared.tables["t_pl"]["Particulars"])
    assert labels[0] == "Depreciation" and labels[2] == "Revenue"
    assert labels[1] == "- Particulars A"                      # LLM off: the spec fallback, prefix kept
