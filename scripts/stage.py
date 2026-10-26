"""Run report stages for development: python scripts/stage.py --pdf PDF --until style|templates|...

Each stage writes <dir>/<stage>/output.json and done.json (default dir: artifacts/runs/dev), and every
file it makes goes under that folder too; a rerun reuses a stage whose inputs have not changed.
`--fresh` re-runs everything. The pipeline run (and its blueprint) used is the newest one made from
the `--pdf` file (E-37), never another PDF's run.

The style stages (M8.1-M8.3) are wired as they land; calling a stage that is not implemented yet
stops with a clear NotImplementedError naming its milestone.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.llm import make_gateways  # noqa: E402
from src.stage_runner import STAGE_NAMES, Stage, run_stages  # noqa: E402


def _document(cfg, pdf: Path):
    """The Source Document of THIS pdf, from its pipeline run (E-37)."""
    from src.blueprint import to_source_document
    blueprint_path = _latest_run(cfg, pdf) / "blueprint.json"
    return to_source_document(json.loads(blueprint_path.read_text(encoding="utf-8")))


def _not_yet(stage: str, milestone: str):
    def run(_cache):
        raise NotImplementedError(f"stage {stage!r} lands in {milestone}")
    return run


def _latest_run(cfg, pdf: Path) -> Path:
    """The newest finished pipeline run made from `pdf` (matched by the PDF's sha256)."""
    from src.extraction import sha256_file
    sha = sha256_file(pdf)
    base = Path(cfg.artifacts_dir) / "runs"
    for manifest in sorted(base.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime,
                           reverse=True):
        run_dir = manifest.parent
        try:
            source = json.loads(manifest.read_text(encoding="utf-8")).get("source_sha256")
        except (json.JSONDecodeError, OSError):
            continue
        if source == sha and (run_dir / "inference.json").exists() and (run_dir / "canonical_dataset").exists():
            return run_dir
    raise FileNotFoundError(f"no pipeline run of {pdf.name} found; run scripts/run_decraft.py {pdf} first")


def _load_run_dataset(run_dir: Path, cfg):
    import pandas as pd

    from src.contracts import ChartSpec, Fact, InferenceReport, SCHEMA_VERSION, ValidationReport
    from src.contracts.dataset import CanonicalDataset

    report = InferenceReport.model_validate_json(
        (run_dir / "inference.json").read_text(encoding="utf-8"))
    tables = {path.stem: pd.read_csv(path) for path in
              sorted((run_dir / "canonical_dataset").glob("*.csv"))}
    display = {path.stem: pd.read_csv(path) for path in sorted((run_dir / "display").glob("*.csv"))}
    facts_path = run_dir / "facts.json"
    facts = ({key: Fact.model_validate(value) for key, value in
              json.loads(facts_path.read_text(encoding="utf-8")).items()}
             if facts_path.exists() else {})
    charts_path = run_dir / "charts.json"
    charts = ({key: ChartSpec.model_validate(value) for key, value in
               json.loads(charts_path.read_text(encoding="utf-8")).items()}
              if charts_path.exists() else {})
    lineage_path = run_dir / "lineage.json"
    lineage = (json.loads(lineage_path.read_text(encoding="utf-8"))
               if lineage_path.exists() else {})
    validation = ValidationReport.model_validate_json(
        (run_dir / "validation_report.json").read_text(encoding="utf-8"))
    data = CanonicalDataset(
        dataset_id=run_dir.name, schema_version=SCHEMA_VERSION, seed=cfg.seed, tables=tables,
        display=display, key_columns=report.key_columns,
        rules=[rule for rule in report.rules if rule.enforcement.value == "hard"], facts=facts,
        charts=charts, lineage=lineage, validation=validation, source_profiles=report.profiles)
    return data, report


def build_stages(pdf: Path, cfg, root: Path | None = None, dry_run: bool = False) -> list[Stage]:
    root = Path(root) if root is not None else ROOT / "artifacts" / "runs" / "dev"
    from src.style import build_templates, extract_style

    def style(_cache):
        try:
            return extract_style(pdf, cfg, out_dir=root / "style").model_dump(mode="json")
        except NotImplementedError:
            raise NotImplementedError("stage 'style' lands in M8.1") from None

    def templates(cache):
        try:
            from src.contracts.report import StyleProfile
            style_value = StyleProfile.model_validate(cache["style"])
            return [page.model_dump(mode="json")
                    for page in build_templates(pdf, _document(cfg, pdf), style_value, cfg)]
        except NotImplementedError:
            raise NotImplementedError("stage 'templates' lands in M8.2") from None

    def figures(cache):
        from collections import Counter

        from src.contracts.report import PageTemplate
        from src.style import analyse_figures
        pages = [PageTemplate.model_validate(page) for page in cache["templates"]]
        gateway = make_gateways(cfg)["vision"] if cfg.llm_enabled else None
        found, recovered = analyse_figures(pdf, pages, cfg, gateway)
        kinds = Counter(figure.kind for figure in found)
        chart_kinds = Counter(figure.chart_kind for figure in found if figure.kind == "chart")
        return {"charts": dict(chart_kinds), "photos": kinds.get("photo", 0),
                "logos": kinds.get("logo", 0), "figures": len(found),
                "items": [figure.model_dump(mode="json") for figure in found],
                "recovered": [chart.model_dump(mode="json") for chart in recovered],
                "recovered_items": [chart.model_dump(mode="json") for chart in recovered],
                "llm_calls": len(gateway.log) if gateway else 0}

    def charts(cache):
        from collections import Counter

        import pandas as pd

        from src.generation import register_all_charts
        from src.rendering._charts import (assert_chart_matches_table, contact_sheet, draw_png,
                                           draw_svg, write_sidecar)

        run_dir = _latest_run(cfg, pdf)
        data, report = _load_run_dataset(run_dir, cfg)
        prepared_dir = root / "prepare"
        if (prepared_dir / "canonical_dataset").exists():
            data.tables = {path.stem: pd.read_csv(path) for path in
                           sorted((prepared_dir / "canonical_dataset").glob("*.csv"))}
            data.display = {path.stem: pd.read_csv(path) for path in
                            sorted((prepared_dir / "display").glob("*.csv"))}
        plan_path = run_dir / "document_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        data.charts = register_all_charts(data, plan)

        out = root / "charts"
        out.mkdir(parents=True, exist_ok=True)
        pngs, matches = [], True
        for chart_id, spec in data.charts.items():
            draw_svg(data, chart_id, out / f"{chart_id}.svg")
            pngs.append(draw_png(data, chart_id, out / f"{chart_id}.png"))
            sidecar = write_sidecar(data, chart_id, out / f"{chart_id}.json")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if spec.orientation == "columns":
                matches = matches and assert_chart_matches_table(
                    payload["series"], data.display[spec.table_id], list(payload["series"]))
            else:
                matches = matches and bool(payload["series"])
        sheet = contact_sheet(pngs, out / "contact_charts.png")
        displayed = {spec.table_id for spec in data.charts.values() if spec.display}
        chartable, unchartable = [], []
        for table_id, frame in data.display.items():
            has_numbers = any(pd.api.types.is_numeric_dtype(frame[column])
                              and pd.to_numeric(frame[column], errors="coerce").notna().any()
                              for column in frame.columns)
            (chartable if has_numbers else unchartable).append(table_id)
        return {"charts": len(data.charts),
                "kinds": dict(Counter(spec.kind for spec in data.charts.values())),
                "sidecars_match": bool(matches),
                "every_displayed_table": all(table in displayed for table in chartable),
                "unchartable": unchartable,
                "contact_sheet": str(sheet) if sheet else None}

    def plan_stage(cache):
        from collections import Counter

        import pandas as pd

        from src.blueprint import to_source_document
        from src.contracts import Fact
        from src.contracts.report import PageTemplate
        from src.narrative import plan as build_plan

        run_dir = _latest_run(cfg, pdf)
        data, report = _load_run_dataset(run_dir, cfg)
        prepared_dir = root / "prepare"                     # E-42: the stage folder, not a fixed one
        if (prepared_dir / "canonical_dataset").exists():
            data.tables = {path.stem: pd.read_csv(path) for path in
                           sorted((prepared_dir / "canonical_dataset").glob("*.csv"))}
            data.display = {path.stem: pd.read_csv(path) for path in
                            sorted((prepared_dir / "display").glob("*.csv"))}
        facts_path = prepared_dir / "facts.json"
        if facts_path.exists():
            data.facts = {key: Fact.model_validate(value) for key, value in
                          json.loads(facts_path.read_text(encoding="utf-8")).items()}
        templates = [PageTemplate.model_validate(page) for page in cache["templates"]]
        blueprint = json.loads((run_dir / "blueprint.json").read_text(encoding="utf-8"))
        document = to_source_document(blueprint)
        gateway = make_gateways(cfg)["text"] if cfg.llm_enabled else None
        outline, bible = build_plan(data, templates, document, cfg, gateway)
        return {"report_title": outline.report_title,
                "sections": [{"sid": section.section_id, "source_title": section.source_title,
                              "title": section.title, "goal": section.goal,
                              "blocks": len(section.block_ids),
                              "facts": len(section.fact_keys)} for section in outline.sections],
                "bible": {"entities": len(bible.entities),
                          "kinds": dict(Counter(entity.kind for entity in bible.entities)),
                          "dates": sorted({value for entity in bible.entities
                                           for key, value in entity.attrs.items()
                                           if key in {"year", "date"}})},
                "outline": [section.model_dump(mode="json") for section in outline.sections],
                "bible_entities": [entity.model_dump(mode="json") for entity in bible.entities]}

    def prepare(cache):
        import numpy as np
        from collections import Counter

        from src.blueprint import to_source_document
        from src.contracts.report import FigureInfo, PageTemplate, RecoveredChart
        from src.generation import prepare_for_report, register_charts
        import pandas as pd

        from src.core.originality import is_generic_label

        run_dir = _latest_run(cfg, pdf)
        data, report = _load_run_dataset(run_dir, cfg)
        plan_path = run_dir / "document_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        data.charts = register_charts(data, plan)
        templates = [PageTemplate.model_validate(page) for page in cache["templates"]]
        figures = [FigureInfo.model_validate(item) for item in cache["figures"].get("items", [])]
        recovered = [RecoveredChart.model_validate(item)
                     for item in cache["figures"].get("recovered_items", [])]
        blueprint = json.loads((run_dir / "blueprint.json").read_text(encoding="utf-8"))
        document = to_source_document(blueprint)
        before = {table_id: len(frame) for table_id, frame in data.tables.items()}
        gateway = make_gateways(cfg)["text"] if cfg.llm_enabled else None
        # E-35 / E-39: the run's Label Fabricator already decided the labels; prepare reuses them
        from src.contracts import PrivacyPlan
        plan_file = run_dir / "privacy_plan.json"
        privacy_plan = (PrivacyPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
                        if plan_file.exists() else None)
        prepared = prepare_for_report(data, report, figures, recovered, templates, cfg,
                                      np.random.default_rng(cfg.seed), gateway=gateway, doc=document,
                                      privacy_plan=privacy_plan, out_dir=root / "prepare")
        stage_dir = root / "prepare"
        (stage_dir / "canonical_dataset").mkdir(parents=True, exist_ok=True)
        (stage_dir / "display").mkdir(parents=True, exist_ok=True)
        for table_id, frame in prepared.tables.items():
            frame.to_csv(stage_dir / "canonical_dataset" / f"{table_id}.csv", index=False)
        for table_id, frame in prepared.display.items():
            frame.to_csv(stage_dir / "display" / f"{table_id}.csv", index=False)
        (stage_dir / "facts.json").write_text(
            json.dumps({key: fact.model_dump(mode="json") for key, fact in prepared.facts.items()},
                       indent=2), encoding="utf-8")
        labels_path = stage_dir / "labels.json"
        avoid_path = stage_dir / "avoid.json"
        labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
        avoid = json.loads(avoid_path.read_text(encoding="utf-8")) if avoid_path.exists() else []
        generic = sorted({str(value)
                          for frame in prepared.tables.values()
                          for column in frame.columns
                          if not (pd.api.types.is_numeric_dtype(frame[column])
                                  or pd.api.types.is_datetime64_any_dtype(frame[column]))
                          for value in frame[column].dropna().unique()
                          if is_generic_label(str(value))})[:12]
        return {"shift": cfg.report.period_shift_years,
                "allowed_periods": prepared.allowed_periods,
                "labels": len(labels), "replaced": sum(1 for src, new in labels.items() if src != new),
                "kept_generic": generic, "avoid_size": len(avoid),
                "kept_by_fabricator": (sum(len(v) for v in privacy_plan.kept_labels.values())
                                       if privacy_plan is not None else None),
                "rows": {table_id: [before[table_id], len(prepared.tables[table_id])]
                         for table_id in prepared.tables},
                "facts": len(prepared.facts),
                "bands": dict(Counter(fact.band for fact in prepared.facts.values() if fact.band))}

    def write_stage(cache):
        import pandas as pd

        from src.contracts import Fact
        from src.contracts.report import (BibleEntity, Outline, PageTemplate, SectionPlan,
                                          StoryBible)
        from src.llm import estimate_tokens
        from src.narrative._prompts import WRITER
        from src.narrative._writer import build_writer_payload, write_blocks

        run_dir = _latest_run(cfg, pdf)
        data, report = _load_run_dataset(run_dir, cfg)
        prepared_dir = root / "prepare"                     # E-42: the stage folder, not a fixed one
        if (prepared_dir / "canonical_dataset").exists():
            data.tables = {path.stem: pd.read_csv(path) for path in
                           sorted((prepared_dir / "canonical_dataset").glob("*.csv"))}
            data.display = {path.stem: pd.read_csv(path) for path in
                            sorted((prepared_dir / "display").glob("*.csv"))}
        facts_path = prepared_dir / "facts.json"
        if facts_path.exists():
            data.facts = {key: Fact.model_validate(value) for key, value in
                          json.loads(facts_path.read_text(encoding="utf-8")).items()}
        plan_output = cache["plan"]                         # the plan stage's output, from this --dir
        outline = Outline(report_title=plan_output["report_title"],
                          sections=[SectionPlan.model_validate(item)
                                    for item in plan_output["outline"]])
        bible = StoryBible(version=1, entities=[BibleEntity.model_validate(item)
                                                for item in plan_output["bible_entities"]])
        templates = [PageTemplate.model_validate(page) for page in cache["templates"]]
        blocks = {block.block_id: block for page in templates for block in page.blocks}
        plan_file = run_dir / "privacy_plan.json"                # E-28: the run's avoid list
        avoid = (json.loads(plan_file.read_text(encoding="utf-8")).get("avoid")
                 if plan_file.exists() else None)
        if dry_run:
            from src.core.originality import tone_profile
            tone = tone_profile([b.text for b in blocks.values() if b.kind == "text" and b.text])
            cap = max(200, WRITER.max_input_tokens - estimate_tokens(WRITER.system))
            rows = []
            for section in outline.sections:
                full, _ = build_writer_payload(section, outline, bible, data.facts,
                                               list(data.allowed_periods), 10 ** 6, blocks,
                                               tone=tone, avoid=avoid)
                tokens = estimate_tokens(full)
                fields = json.loads(full)
                rows.append({"sid": section.section_id, "tokens": tokens, "cap": cap,
                             "over_cap": tokens > cap,
                             "has_dt_dom_tone_avoid": all(k in fields for k in ("dt", "dom", "tone", "avoid"))})
            return {"dry_run": True, "cap": cap, "sections": rows}
        gateway = make_gateways(cfg)["text"] if cfg.llm_enabled else None
        doc = write_blocks(data, outline, bible, templates, None, cfg, gateway, avoid=avoid)
        return {"words": doc.word_count, "llm_used": doc.llm_used, "flags": doc.flags,
                "sections": [{"sid": section.section_id, "title": section.title,
                              "words": len(section.text.split()),
                              "ok": not any(flag.startswith(f"{section.section_id}: fallback")
                                            for flag in doc.flags),
                              "flags": [flag for flag in doc.flags
                                        if flag.startswith(section.section_id)]}
                             for section in doc.sections]}

    return [
        Stage("style", style),
        Stage("templates", templates),
        Stage("figures", figures),
        Stage("prepare", prepare),
        Stage("charts", charts),
        Stage("plan", plan_stage),
        Stage("write", write_stage),
        Stage("fit", _not_yet("fit", "M8.9")),
        Stage("images", _not_yet("images", "M8.10")),
        Stage("render", _not_yet("render", "M8.11")),
        Stage("qa", _not_yet("qa", "M8.12")),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeCraft report stages up to one stage.")
    parser.add_argument("--pdf", default=str(ROOT / "data" / "input" / "sample_input_1.pdf"))
    parser.add_argument("--until", required=True, choices=STAGE_NAMES)
    parser.add_argument("--dir", default=str(ROOT / "artifacts" / "runs" / "dev"))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--llm", action="store_true", help="use the provider gateways when keys exist")
    parser.add_argument("--dry-run", action="store_true", help="write stage: payload sizes only")
    args = parser.parse_args()
    stages = build_stages(Path(args.pdf), load_config(llm_enabled=args.llm), Path(args.dir),
                          dry_run=args.dry_run)
    run_stages(stages, args.until, Path(args.dir), fresh=args.fresh,
               salt="dry-run" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
