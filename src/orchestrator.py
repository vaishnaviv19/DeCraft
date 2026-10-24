# src/orchestrator.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from src.inference.config import Config
from src.inference.infer_domain import infer_domain
from src.inference.infer_relationships import find_relationships
from src.inference.infer_constraints import infer_constraints
try:
    from src.inference.infer_semantic_relations_llm import infer_semantic_relations_with_llm
except Exception:
    def infer_semantic_relations_with_llm(tables):
        return []
from src.inference.dependency_graph import build_graph
from src.generator.privacy_analysis import analyze_privacy
from src.generator.validate_data import validate_dataset
from src.generator.repair_data import repair_dataset
from src.generator.build_dataset import build_dataset
from src.inference.schema import InferenceReport, DomainHypothesis


class HardConstraintError(Exception):
    pass


def run_inference(blueprint_path: str = "outputs/blueprint/blueprint.json") -> InferenceReport:
    cfg = Config.from_env()
    bp = json.loads(Path(blueprint_path).read_text(encoding="utf-8"))
    # try LLM domain first, fallback
    try:
        from src.agents.domain_agent import DomainAgent
        from src.agents.llm_client import LLMClient
        tables_tmp = {}
        for t in bp.get("tables", []):
            try:
                csv_path = t.get("source_csv")
                if csv_path and Path(csv_path).exists():
                    tables_tmp[t["table_id"]] = pd.read_csv(csv_path)
            except Exception:
                continue
        d = DomainAgent(LLMClient()).infer_domain(tables_tmp, bp.get("headings", []))
        domain = {"label": d.get("label", "unknown"), "confidence": d.get("confidence", 0.0), "evidence": d.get("evidence", [])}
        if domain["label"] == "unknown":
            domain = infer_domain(bp.get("headings", []), bp.get("text_blocks", []))
    except Exception:
        domain = infer_domain(bp.get("headings", []), bp.get("text_blocks", []))
    # load tables
    tables = {}
    for t in bp.get("tables", []):
        try:
            csv_path = t.get("source_csv")
            if csv_path and Path(csv_path).exists():
                tables[t["table_id"]] = pd.read_csv(csv_path)
        except Exception:
            continue
    rels = find_relationships(tables)
    # cross-table from LLM if available
    try:
        from src.agents.relationship_agent import RelationshipAgent
        from src.agents.llm_client import LLMClient
        r2 = RelationshipAgent(LLMClient()).propose_cross_table(tables)
        # validate simple FK
        for p in r2:
            try:
                pt, pk = p.get("parent_table"), p.get("parent_key")
                ct, ck = p.get("child_table"), p.get("child_key")
                if pt in tables and ct in tables and pk in tables[pt].columns and ck in tables[ct].columns:
                    s1 = set(tables[pt][pk].astype(str).unique())
                    s2 = set(tables[ct][ck].astype(str).unique())
                    if s1 and s2 and len(s1 & s2) / len(s2) >= 0.5:
                        rels.append({"id": f"llm_rel_{ct}_{ck}_{pt}_{pk}", "parent_table": pt, "parent_key": pk, "child_table": ct, "child_key": ck, "cardinality": "one_to_many", "overlap_rate": len(s1 & s2) / len(s2), "confidence": 0.7, "evidence": [{"source": "cross_table", "detail": "LLM proposed, validated", "weight": 0.7}]})
            except Exception:
                continue
    except Exception:
        pass
    cons = infer_constraints(tables)
    # add semantic relations via LLM agent (code validates)
    cons.extend(infer_semantic_relations_with_llm(tables))
    g = build_graph(cons, rels)
    privacy = analyze_privacy(tables, bp.get("entity", {}).get("entity_name"), blueprint=bp)
    report = InferenceReport(
        source_pdf=bp.get("source", {}).get("pdf_path", ""),
        source_hash="",
        blueprint_version="1.0",
        seed=cfg.seed,
        domain=DomainHypothesis(label=domain["label"], confidence=domain["confidence"], evidence=domain["evidence"]),
        relationships=rels,
        constraints=cons,
        privacy=privacy,
        dependency_order=g.graph.get("order", []),
        lineage={"methods": ["profile", "relationships_llm_proposed_validated", "correlation_based", "semantic_relations_llm_validated", "llm_privacy_advice"]},
    )
    return report


def run_generation(report: InferenceReport, blueprint_path: str = "outputs/blueprint/blueprint.json"):
    cfg = Config.from_env()
    bp = json.loads(Path(blueprint_path).read_text(encoding="utf-8"))
    tables = {}
    for t in bp.get("tables", []):
        try:
            csv_path = t.get("source_csv")
            if csv_path and Path(csv_path).exists():
                tables[t["table_id"]] = pd.read_csv(csv_path)
        except Exception:
            continue
    cons = [c.model_dump() for c in report.constraints]
    # validate
    rep = validate_dataset(tables, cons)
    ds = tables
    if not rep["ok"]:
        if not rep["repairable"]:
            raise HardConstraintError(str(rep))
        for attempt in range(cfg.max_repairs):
            rep = validate_dataset(ds, cons)
            if rep["ok"]:
                break
            ds = repair_dataset(ds, rep, cons, attempt)
        else:
            rep2 = validate_dataset(ds, cons)
            if not rep2["ok"]:
                raise HardConstraintError(str(rep2))
    build_dataset(ds, report)
    return ds
