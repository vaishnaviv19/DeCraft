# tests/narrative/test_write.py
import json

import numpy as np

from src.config import load_config
from src.contracts import SourceDocument, SourceTable
from src.generation import generate
from src.inference import infer
from src.narrative import write
from src.privacy import plan_privacy


def plan(sections: int = 6) -> dict:
    return {"sections": [{"section_id": f"sec_{i + 1:03d}", "title": f"Performance review {i + 1}",
                          "purpose": "Explain the figures", "target_word_count": 200,
                          "tables": [{"table_id": "t_sales"}], "charts": []}
                         for i in range(sections)]}


def generated(cfg):
    values = [(120, 45), (80, 50), (150, 42), (95, 48), (110, 46), (70, 52)]
    rows = [[f"A-{101 + i}", "North", str(u), str(p), str(u * p)]
            for i, (u, p) in enumerate(values)]
    doc = SourceDocument(document_sha256="abc", text_blocks=["text"],
                         tables=[SourceTable(table_id="t_sales", page=1,
                                             header=["Ref", "Region", "Units", "Unit Price", "Revenue"],
                                             raw_cells=rows)])
    report = infer(doc, cfg)
    return generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))


def test_llm_off_path_writes_at_least_a_thousand_words_without_placeholders(cfg):
    data = generated(cfg)
    doc = write(data, plan(6), cfg)
    assert doc.word_count >= 1000 and doc.llm_used is False
    assert len(doc.sections) == 6
    for section in doc.sections:
        assert "{{" not in section.text and "}}" not in section.text
        assert section.title


def test_a_draft_with_a_stray_digit_is_rejected_and_the_template_is_used(cfg, gateway_for):
    from src.llm import PromptSpec  # noqa: F401  (the FakeLLM reply shape is what matters)
    from tests.conftest import FakeLLM

    bad = json.dumps({"text": "Revenue was 1234 in the period."})
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    data = generated(llm_cfg)
    doc = write(data, plan(1), llm_cfg, gateway=gateway_for(FakeLLM([bad])))
    assert doc.llm_used is False and "1234" not in doc.sections[0].text


def test_a_valid_draft_is_used(cfg, gateway_for):
    from tests.conftest import FakeLLM

    data = generated(cfg)
    key = "t_sales.Revenue.total"
    good = json.dumps({"text": "Revenue reached {{ " + key + "|:0 }} in the period."})
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    doc = write(data, plan(1), llm_cfg, gateway=gateway_for(FakeLLM([good])))
    assert doc.llm_used is True and "{{" not in doc.sections[0].text
