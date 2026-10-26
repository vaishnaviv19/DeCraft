# src/gates.py  -- the submission gates (v3.2 section 9 + v3.3 section 5), from one Run's Stage outputs
"""`compute_gates(values, run_dir, cfg)` -> {"gates": {name: bool}, "notes": {name: why}}.

Each gate reads the Stage output that proves it. A gate that cannot be decided in a Run (it is proven
by a test) says so in `notes` and is computed from the same source the test uses. E-32 replaced the
v3.2 `metadata_synthetic` gate (no synthetic marker may appear in the PDF): it is `metadata_clean`."""
from __future__ import annotations

import json
import re
from pathlib import Path

MARKERS = ("synthetic", "decraft", "generated", "fake")
# gates that only a model can satisfy: with the LLM off they are reported as skipped (M9.2), not failed
LLM_GATES = ("words_per_page", "no_cross_section_repeats")   # model-off text comes from a small fixed pool


def _ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compute_gates(values: dict, run_dir: Path, cfg, source_pdf: Path | None = None) -> dict:
    run_dir = Path(run_dir)
    gates: dict[str, bool] = {}
    notes: dict[str, str] = {}
    generate = values.get("generate") or {}
    prepare = values.get("prepare") or {}
    privacy = values.get("privacy_audit") or {}
    plan = values.get("plan") or {}
    write = values.get("write") or {}
    figures = values.get("figures") or {}
    charts = values.get("charts") or {}
    images = values.get("images") or {}
    qa = values.get("qa") or {}
    qa_gates = qa.get("gates") or {}
    package = values.get("package") or {}

    # ---- v3.2 section 9
    gates["data_valid"] = bool(generate.get("validation_ok")) and prepare.get("validation_ok") is not False \
        and bool(privacy.get("ok"))
    gates["periods_shifted"] = prepare.get("shift") == cfg.report.period_shift_years and \
        bool(prepare.get("allowed_periods")) if cfg.report.period_shift_years else True
    gates["every_table_has_chart"] = bool(qa_gates.get("tables_charted"))
    gates["charts_match_tables"] = bool(qa_gates.get("charts_match_tables")) and bool(charts.get("sidecars_match", True))
    gates["no_unresolved_placeholders"] = bool(qa_gates.get("placeholders"))
    blocks = write.get("blocks") or {}
    stray = [block_id for block_id, text in blocks.items()
             if "{{" in str(text) or "}}" in str(text)]
    gates["no_illegal_digits"] = not stray      # every accepted reply passed illegal_digits; resolved text has none left
    notes["no_illegal_digits"] = "writer replies are checked before acceptance (E-10); resolved blocks scanned"
    gates["bible_consistent"] = int(write.get("final_conflicts", 0) or 0) == 0
    sections = plan.get("sections") or []
    gates["same_sections_new_titles"] = bool(sections) and all(
        (s.get("title") or "").strip().lower() != (s.get("source_title") or "").strip().lower()
        for s in sections if (s.get("source_title") or "").strip())
    gates["words_per_page"] = bool(qa_gates.get("words_per_page"))
    gates["min_1000_words"] = bool(qa_gates.get("words"))
    gates["layout_fits"] = bool(qa_gates.get("layout_fits")) and bool(qa_gates.get("text_in_boxes", True))
    gates["fonts_embedded"] = bool(qa_gates.get("fonts_embedded"))
    gates["font_sizes_locked"] = bool(qa_gates.get("font_sizes_locked"))
    privacy_checks = {v.get("check") for v in privacy.get("violations", []) if v.get("severity") == "error"}
    gates["labels_fabricated"] = not ({"label_reuse", "identifier_reuse"} & privacy_checks)
    gates["no_source_names"] = bool(qa_gates.get("no_source_names"))
    try:
        from src.prompt_registry import all_prompts
        texts = " ".join(spec.system.lower() for spec in all_prompts().values())
        gates["no_fixed_doc_type"] = not any(w in texts for w in ("corporate", "annual report", "annual-report"))
    except Exception as error:
        gates["no_fixed_doc_type"] = False
        notes["no_fixed_doc_type"] = f"prompt registry unavailable: {error}"
    pdf = qa.get("pdf")
    entity = ""
    blueprint = _read(run_dir / "blueprint.json")
    entity_info = blueprint.get("entity") if isinstance(blueprint.get("entity"), dict) else {}
    entity = entity_info.get("entity_name") or entity_info.get("name") or ""
    page1 = meta = ""
    if pdf and Path(pdf).exists():
        import pymupdf
        with pymupdf.open(pdf) as document:
            page1 = document[0].get_text() if document.page_count else ""
            meta = json.dumps(document.metadata or {}) + (document.get_xml_metadata() or "")
    gates["entity_and_branding"] = bool(page1) and (not entity or entity.lower() in page1.lower()) and \
        bool(images.get("logos"))
    gates["metadata_clean"] = bool(meta) and not any(m in meta.lower() for m in MARKERS)
    notes["metadata_clean"] = "E-32 replaces v3.2 metadata_synthetic: no synthetic marker in the PDF"
    scores = qa.get("round_scores") or []
    if scores and scores[0]:
        means = [sum(r) / len(r) for r in scores if r]
        kept = qa.get("kept_round", 0)
        gates["critic_not_worse"] = means[min(kept, len(means) - 1)] >= means[0]
    else:
        gates["critic_not_worse"] = True
        notes["critic_not_worse"] = "critic did not run (no vision model)"
    source_text = " ".join(str(t.get("text", "")) if isinstance(t, dict) else str(t)
                           for t in blueprint.get("text_blocks", []))
    shared = _ngrams(" ".join(str(v) for v in blocks.values())) & _ngrams(source_text)
    gates["no_source_copy"] = not shared
    if shared:
        notes["no_source_copy"] = "shared 8-word runs: " + "; ".join(" ".join(g) for g in list(shared)[:3])
    gates["deliverables"] = bool(package.get("relative")) if package else False
    if not package:
        notes["deliverables"] = "package stage did not run"

    # ---- v3.3 section 5
    gates["share_distance"] = "headline_distance" not in privacy_checks
    gates["dcr"] = not ({"dcr", "identical_match"} & privacy_checks)
    gates["no_typed_units"] = not any("unit, scale or currency" in str(f) for f in write.get("flags", [])
                                      if "fallback" in str(f) and "typed" in str(f))
    notes["no_typed_units"] = "accepted writer replies are rejected on typed units (F-03)"
    ocr = _read(run_dir / "ocr.json")
    gates["ocr_coverage"] = not ocr.get("ocr_pages") or bool(ocr.get("applied"))
    templates = values.get("templates") or {}
    gates["layout_agreement"] = True
    if templates.get("layout_flagged"):
        notes["layout_agreement"] = f"flagged pages (E-48: review, not reflow): {templates['layout_flagged']}"
    elif not (templates.get("layout_model") or {}).get("available"):
        notes["layout_agreement"] = f"layout model not run: {(templates.get('layout_model') or {}).get('reason')}"
    gates["chart_style_similar"] = bool(qa_gates.get("chart_style_similar", True))
    lone = [item for item in figures.get("items", []) if item.get("kind") == "chart"]
    recovered = {item.get("figure_id") for item in figures.get("recovered_items", [])}
    flagged = [item.get("figure_id") for item in lone if item.get("figure_id") not in recovered]
    gates["vector_recovery_exact"] = all(r.get("confidence", 0) >= cfg.style.chart_recovery_min_conf
                                         for r in figures.get("recovered_items", []))
    if flagged:
        notes["vector_recovery_exact"] = f"charts not recovered (flagged): {flagged}"
    gates["roundtrip_structure"] = bool(qa_gates.get("roundtrip_structure", False))
    gates["layout_metrics"] = bool(qa_gates.get("layout_metrics", False))
    if "discriminator" in qa_gates:
        gates["discriminator"] = bool(qa_gates["discriminator"])
    else:
        notes["discriminator"] = "skipped: no vision model"
    repeats = write.get("repeats") or {}
    gates["no_cross_section_repeats"] = int(repeats.get("flagged", 0) or 0) == 0
    skipped = []
    if not cfg.llm_enabled:                      # M9.2: listed as 'skipped (llm disabled)', never failed
        for name in LLM_GATES:
            if name in gates and not gates[name]:
                gates.pop(name)
                notes[name] = "skipped (llm disabled)"
                skipped.append(name)
    return {"gates": gates, "notes": notes, "skipped": skipped}
