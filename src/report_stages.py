# src/report_stages.py  -- the v3.2 report stages (M8.1-M8.12), shared by pipeline.run and scripts/stage.py
"""Report stages after generation: style -> templates -> figures -> prepare -> charts -> plan -> write ->
fit -> images -> render -> qa.

Each stage reads the Run Directory of the generation half (blueprint, inference report, canonical
dataset, privacy plan) and the outputs of earlier report stages (`cache`), and returns a JSON-able
summary that the stage runner saves as `<root>/<stage>/output.json`. Files a stage makes go under
`<root>/<stage>/`.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.stage_runner import Stage


# ---------------------------------------------------------------- loading the generation half
def latest_run(cfg, pdf: Path) -> Path:
    """The newest generation run made from `pdf` (matched by the PDF's sha256, E-37)."""
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


def load_run_dataset(run_dir: Path, cfg):
    import pandas as pd

    from src.contracts import ChartSpec, Fact, InferenceReport, SCHEMA_VERSION, ValidationReport
    from src.contracts.dataset import CanonicalDataset

    report = InferenceReport.model_validate_json((run_dir / "inference.json").read_text(encoding="utf-8"))
    tables = {path.stem: pd.read_csv(path) for path in sorted((run_dir / "canonical_dataset").glob("*.csv"))}
    display = {path.stem: pd.read_csv(path) for path in sorted((run_dir / "display").glob("*.csv"))}

    def read(name, default):
        path = run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    facts = {key: Fact.model_validate(value) for key, value in read("facts.json", {}).items()}
    charts = {key: ChartSpec.model_validate(value) for key, value in read("charts.json", {}).items()}
    validation = ValidationReport.model_validate_json(
        (run_dir / "validation_report.json").read_text(encoding="utf-8"))
    data = CanonicalDataset(
        dataset_id=run_dir.name, schema_version=SCHEMA_VERSION, seed=cfg.seed, tables=tables,
        display=display, key_columns=report.key_columns,
        rules=[rule for rule in report.rules if rule.enforcement.value == "hard"], facts=facts,
        charts=charts, lineage=read("lineage.json", {}), validation=validation,
        source_profiles=report.profiles)
    return data, report


def _prepared(data, root: Path, cfg):
    """Swap in the prepare stage's tables, display tables, facts and periods (E-42: this run's folder)."""
    import pandas as pd

    from src.contracts import Fact

    prepared_dir = root / "prepare"
    if (prepared_dir / "canonical_dataset").exists():
        data.tables = {path.stem: pd.read_csv(path)
                       for path in sorted((prepared_dir / "canonical_dataset").glob("*.csv"))}
        data.display = {path.stem: pd.read_csv(path)
                        for path in sorted((prepared_dir / "display").glob("*.csv"))}
    facts_path = prepared_dir / "facts.json"
    if facts_path.exists():
        data.facts = {key: Fact.model_validate(value)
                      for key, value in json.loads(facts_path.read_text(encoding="utf-8")).items()}
    return data


def _display_tables(root: Path) -> dict:
    """The printed tables: the charts stage's copies (rows capped to their boxes, M8.5.2), else prepare's."""
    import pandas as pd
    display_dir = root / "charts" / "display"
    if not display_dir.exists():
        display_dir = root / "prepare" / "display"
    return ({path.stem: pd.read_csv(path) for path in sorted(display_dir.glob("*.csv"))}
            if display_dir.exists() else {})


def _chart_files(root: Path) -> tuple[dict[str, str], dict[str, Path]]:
    """(table_id -> chart SVG, table_id -> sidecar JSON) of the charts stage."""
    charts, sidecars = {}, {}
    for path in sorted((root / "charts").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or not payload.get("table_id"):
            continue
        sidecars[payload["table_id"]] = path
        svg = path.with_suffix(".svg")
        if svg.exists():
            charts[payload["table_id"]] = str(svg)
    return charts, sidecars


def keep_rows(frame, rows: int, charts: dict, table_id: str):
    """The `rows` rows a capped table keeps (M8.5.2/E-17): the window holding the line items its chart
    plots (and the sub-header row just above them), so the chart still matches the printed table;
    otherwise the first rows. Before, a stacked table kept its first 8 rows and lost the shareholding
    rows its chart showed (charts_match_tables failed, 2026-10-26)."""
    from src.core.rows import norm_label
    spec = next((s for s in charts.values() if s.table_id == table_id and s.display), None)
    start = 0
    if spec is not None and spec.orientation == "rows" and spec.series_rows and spec.label_column in frame.columns:
        wanted = {norm_label(r) for r in spec.series_rows}
        positions = [i for i, v in enumerate(frame[spec.label_column]) if norm_label(v) in wanted]
        if positions:
            first = max(0, positions[0] - (1 if spec.x_labels else 0))
            start = max(0, min(first, len(frame) - rows))
    return frame.iloc[start:start + rows].reset_index(drop=True)


def source_chart_styles(pdf, templates) -> dict:
    """{chart block id: ChartStyle} of every SOURCE chart block (v3.3 F-06)."""
    import pymupdf

    from src.style._chart_geometry import chart_style, plot_area, primitives_from_page
    styles = {}
    with pymupdf.open(str(pdf)) as document:
        for page in templates:
            for block in page.blocks:
                if block.kind == "chart" and 1 <= page.page <= document.page_count:
                    prims = primitives_from_page(document[page.page - 1], block.bbox)
                    styles[block.block_id] = chart_style(prims, plot_area(prims, block.bbox))
    return styles


def hidden_charts(data, styles: dict | None = None) -> dict:
    """E-19: a recovered source chart ('rc_<block_id>' table, not printed) is drawn from its generated
    data in the source chart's own box: one line per series, the categories on the x axis, and (F-06)
    the source chart's own colours, chart type and plain lines."""
    from src.contracts import ChartSpec
    specs = {}
    for table_id, frame in data.display.items():
        if not table_id.startswith("rc_") or "Category" not in frame.columns:
            continue
        series = [str(c) for c in frame.columns if str(c) != "Category"]
        if not series:
            continue
        style = (styles or {}).get(table_id[3:])
        kind = "line" if len(frame) > 2 else "bar"
        if style is not None and style.orientation in {"vertical", "horizontal"}:
            kind = "bar" if style.orientation == "vertical" else "horizontal_bar"
        specs[f"chart_{table_id}"] = ChartSpec(
            chart_id=f"chart_{table_id}", table_id=table_id, label_column="Category", series_columns=series,
            title=None, kind=kind, display=False,                                   # the legend names it
            has_legend=len(series) > 1, colors=list(style.colors) if style else [],
            markers=not (style is not None and style.orientation == "line"),
            has_data_labels=bool(style and style.has_data_labels))
    return specs


def _release_fonts() -> None:
    """Close the font files matplotlib cached while drawing: an open FT2Font keeps the extracted
    style/fonts/*.ttf locked on Windows, so a run directory could not be deleted or rewritten."""
    import gc
    try:
        from matplotlib import font_manager
        font_manager._get_font.cache_clear()
    except Exception:
        pass
    gc.collect()


def _chart_font(style, artifacts_root: Path) -> tuple[str | None, float]:
    """(family registered with matplotlib, size pt) of the body Text Style: its extracted font file when
    there is one, else its fallback family (spec M8.5.2: chart fonts are the body font)."""
    from src.rendering._measure import font_path
    body = style.text_styles[0] if style.text_styles else None
    if body is None:
        return None, 9.0
    spec = next((font for font in style.fonts if font.font_id == body.font_id), None)
    path = font_path(spec, artifacts_root)
    try:
        from matplotlib import font_manager
        if path:
            font_manager.fontManager.addfont(path)
            return font_manager.FontProperties(fname=path).get_name(), body.size_pt
    except Exception:
        pass
    return (spec.fallback if spec else None), body.size_pt


def fit_title(title: str, block, style, artifacts_root: Path | None = None) -> str:
    """The title cut at whole words to fit its heading box (E-14: font sizes never change); joiners
    and punctuation left dangling at the end are removed ('Revenue, EBITDA and' -> 'Revenue, EBITDA').
    Before, a long title wrapped in a one-line box and printed 'Revenue,'."""
    import re as _re

    from src.rendering._fit import fits
    from src.rendering._measure import box_for, width_fn_for
    if not title or style is None:
        return title
    box, width = box_for(block, style), width_fn_for(block, style, artifacts_root)
    words = title.split()
    while len(words) > 1 and not fits(" ".join(words), box, width)[0]:
        words = words[:-1]
        while len(words) > 1 and _re.fullmatch(r"(?i)and|or|of|the|to|for|in|&|-|–|,", words[-1]):
            words = words[:-1]
    return " ".join(words).rstrip(",;:-– &")


SPARE_HEADINGS = ["Highlights", "Overview", "Key figures", "Summary", "Details", "Outlook", "Review",
                  "Context", "Breakdown", "Notes", "Background", "Perspective"]


def _table_heading(table, used: set[str]) -> str:
    """A heading for the box right above a table: its first line items ('Revenue, EBITDA and EBIT')."""
    import pandas as pd
    if table is None or not len(table.columns):
        return ""
    labels = []
    for value in table[table.columns[0]].tolist():
        text = str(value).strip()
        if text and text.lower() != "nan" and not any(ch.isdigit() for ch in text) and text not in labels:
            labels.append(text)
        if len(labels) == 3:
            break
    if len(labels) < 2:
        return ""
    title = f"{', '.join(labels[:-1])} and {labels[-1]}"
    return "" if title in used else title


def page_texts(cache: dict, templates, cfg, organisation: str, style=None, tables: dict | None = None) -> dict[str, str]:
    """Text per block as it is printed. EVERY heading box gets a heading (E-48: same layout, only the
    wording changes): the first one of a section shows the section's NEW title; a later one shows a
    heading for the table right below it, or a generic one not used yet. Running header/footer lines keep
    their wording with every date and period moved forward (E-16); a short box naming the organisation
    keeps that name (task 6 branding). Used by render AND by the critic's re-renders."""
    from src.core.periods import shift_text_periods

    images = cache.get("images") or {}
    texts = dict(images.get("blocks") or (cache.get("fit") or {}).get("blocks") or {})
    titles = {item["section_id"]: item.get("title", "")
              for item in (cache.get("plan") or {}).get("outline", [])}
    seen: set[str] = set()
    used: set[str] = set(titles.values())
    spare = [h for h in SPARE_HEADINGS if h not in used]
    for page in templates:
        tables_here = [b for b in page.blocks if b.kind == "table" and b.table_id]
        for block in sorted(page.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            if block.kind != "heading":
                continue
            first = block.section_id not in seen
            seen.add(block.section_id)
            title = titles.get(block.section_id, "") if first else ""
            if not title:                       # a later heading box: name the table below it
                below = [t for t in tables_here if 0 <= t.bbox[1] - block.bbox[3] <= 40
                         and t.bbox[0] < block.bbox[2] and block.bbox[0] < t.bbox[2]]
                if below:
                    title = _table_heading((tables or {}).get(below[0].table_id), used)
            if not title and spare:
                title = spare.pop(0)
            if title:
                used.add(title)
            texts[block.block_id] = fit_title(title, block, style, Path(cfg.artifacts_dir)) if title else ""
    for page in templates:
        for block in page.blocks:
            source_text = (block.text or "").strip()
            if block.kind in {"header", "footer"}:
                texts[block.block_id] = shift_text_periods(source_text, cfg.report.period_shift_years)
            elif organisation and organisation.lower() in source_text.lower() \
                    and block.kind in {"heading", "text"} and len(source_text) <= len(organisation) + 40:
                texts[block.block_id] = organisation
    return texts


def _image_files(cache: dict) -> dict[str, str]:
    images = cache.get("images") or {}
    files = {}
    for item in images.get("items", []):
        if item.get("file") and item.get("slot"):
            files[item["slot"]["block_id"]] = item["file"]
    for logo in images.get("logos", []):
        files[logo["block_id"]] = logo["file"]
    return files


# ---------------------------------------------------------------- the stages
def build_report_stages(pdf: Path, cfg, root: Path, run_dir: Path | None = None,
                        gateways: dict | None = None, dry_run: bool = False) -> list[Stage]:
    """The eleven report stages. `run_dir` is the generation run (default: the newest run of `pdf`);
    `gateways` is {"text","vision","image"} (default: `make_gateways(cfg)` when the LLM is enabled)."""
    pdf, root = Path(pdf), Path(root)
    state: dict[str, Any] = {}

    def run_directory() -> Path:
        if "run_dir" not in state:
            state["run_dir"] = Path(run_dir) if run_dir is not None else latest_run(cfg, pdf)
        return state["run_dir"]

    def gateway(kind: str):
        if not cfg.llm_enabled:
            return None
        if "gateways" not in state:
            if gateways is not None:
                state["gateways"] = gateways
            else:
                from src.llm import make_gateways
                state["gateways"] = make_gateways(cfg)
        found = state["gateways"].get(kind)
        return found if found is not None and getattr(found, "enabled", True) else None

    def document():
        if "doc" not in state:
            from src.blueprint import to_source_document
            blueprint = json.loads((run_directory() / "blueprint.json").read_text(encoding="utf-8"))
            state["doc"] = to_source_document(blueprint)
        return state["doc"]

    def templates_of(cache):
        from src.contracts.report import PageTemplate
        value = cache["templates"]
        pages = value["pages"] if isinstance(value, dict) else value
        return [PageTemplate.model_validate(page) for page in pages]

    def placed_of(cache):
        """Templates with the chart blocks of M8.5.2 (render, images, QA); earlier stages use the plain ones."""
        from src.contracts.report import PageTemplate
        pages = (cache.get("charts") or {}).get("templates")
        return [PageTemplate.model_validate(page) for page in pages] if pages else templates_of(cache)

    def style_of(cache):
        from src.contracts.report import StyleProfile
        return StyleProfile.model_validate(cache["style"])

    def avoid_list() -> list[str]:
        plan_file = run_directory() / "privacy_plan.json"            # E-28: the run's avoid list
        return (json.loads(plan_file.read_text(encoding="utf-8")).get("avoid") or []
                if plan_file.exists() else [])

    def style(_cache):
        from src.style import extract_style
        profile = extract_style(pdf, cfg, out_dir=root / "style")
        return profile.model_dump(mode="json")

    def templates(cache):
        from src.style import build_templates
        from src.style._overlay import write_overlays
        pages = build_templates(pdf, document(), style_of(cache), cfg)
        overlays = write_overlays(pdf, pages, root / "templates")        # M8.2.1 debug overlay
        kinds = {f"p{page.page}": dict(Counter(block.kind for block in page.blocks)) for page in pages}
        from src.report_checks import layout_agreement
        layout = layout_agreement(pdf, pages, cfg, Path(cfg.artifacts_dir))    # v3.3 F-05 (flags only)
        return {"pages": [page.model_dump(mode="json") for page in pages], "kinds": kinds,
                "layout_agreement": layout.get("agreement", {}), "layout_flagged": layout.get("flagged", []),
                "layout_model": {"available": layout.get("available"), "reason": layout.get("reason", "")},
                "mirror": [page.page for page in pages if page.mode == "mirror"],
                "flow": [{"page": page.page, "reason": page.reason} for page in pages if page.mode == "flow"],
                "overlays": [str(path) for path in overlays]}

    def figures(cache):
        from src.style import analyse_figures
        vision = gateway("vision")
        calls_before = len(vision.log) if vision is not None else 0
        found, recovered = analyse_figures(pdf, templates_of(cache), cfg, vision)
        kinds = Counter(figure.kind for figure in found)
        chart_kinds = Counter(figure.chart_kind for figure in found if figure.kind == "chart")
        return {"charts": dict(chart_kinds), "photos": kinds.get("photo", 0),
                "logos": kinds.get("logo", 0), "figures": len(found),
                "items": [figure.model_dump(mode="json") for figure in found],
                "recovered_items": [chart.model_dump(mode="json") for chart in recovered],
                "llm_calls": (len(vision.log) - calls_before) if vision is not None else 0}

    def prepare(cache):
        import numpy as np
        import pandas as pd

        from src.contracts import PrivacyPlan
        from src.contracts.report import FigureInfo, RecoveredChart
        from src.core.originality import is_generic_label
        from src.generation import prepare_for_report, register_charts

        data, report = load_run_dataset(run_directory(), cfg)
        plan_path = run_directory() / "document_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        data.charts = register_charts(data, plan)
        figures_out = cache["figures"]
        found = [FigureInfo.model_validate(item) for item in figures_out.get("items", [])]
        recovered = [RecoveredChart.model_validate(item) for item in figures_out.get("recovered_items", [])]
        before = {table_id: len(frame) for table_id, frame in data.tables.items()}
        plan_file = run_directory() / "privacy_plan.json"       # E-35/E-39: the run's label decisions
        privacy_plan = (PrivacyPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
                        if plan_file.exists() else None)
        stage_dir = root / "prepare"
        prepared = prepare_for_report(data, report, found, recovered, templates_of(cache), cfg,
                                      np.random.default_rng(cfg.seed), gateway=gateway("text"),
                                      doc=document(), privacy_plan=privacy_plan, out_dir=stage_dir)
        (stage_dir / "canonical_dataset").mkdir(parents=True, exist_ok=True)
        (stage_dir / "display").mkdir(parents=True, exist_ok=True)
        for table_id, frame in prepared.tables.items():
            frame.to_csv(stage_dir / "canonical_dataset" / f"{table_id}.csv", index=False)
        for table_id, frame in prepared.display.items():
            frame.to_csv(stage_dir / "display" / f"{table_id}.csv", index=False)
        (stage_dir / "facts.json").write_text(
            json.dumps({key: fact.model_dump(mode="json") for key, fact in prepared.facts.items()},
                       indent=2), encoding="utf-8")
        labels_path, avoid_path = stage_dir / "labels.json", stage_dir / "avoid.json"
        labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
        avoid = json.loads(avoid_path.read_text(encoding="utf-8")) if avoid_path.exists() else []
        generic = sorted({str(value) for frame in prepared.tables.values() for column in frame.columns
                          if not (pd.api.types.is_numeric_dtype(frame[column])
                                  or pd.api.types.is_datetime64_any_dtype(frame[column]))
                          for value in frame[column].dropna().unique()
                          if is_generic_label(str(value))})[:12]
        return {"shift": cfg.report.period_shift_years,
                "allowed_periods": prepared.allowed_periods,
                "validation_ok": bool(prepared.validation.ok) if prepared.validation else None,
                "labels": len(labels), "replaced": sum(1 for src, new in labels.items() if src != new),
                "kept_generic": generic, "avoid_size": len(avoid),
                "kept_by_fabricator": (sum(len(v) for v in privacy_plan.kept_labels.values())
                                       if privacy_plan is not None else None),
                "rows": {table_id: [before.get(table_id), len(prepared.tables[table_id])]
                         for table_id in prepared.tables},
                "facts": len(prepared.facts),
                "bands": dict(Counter(fact.band for fact in prepared.facts.values() if fact.band))}

    def charts(cache):
        import pandas as pd

        from src.generation import register_all_charts
        from src.rendering._charts import (assert_chart_matches_table, contact_sheet, draw_png,
                                           draw_svg, write_sidecar)

        data, _report = load_run_dataset(run_directory(), cfg)
        data = _prepared(data, root, cfg)
        plan_path = run_directory() / "document_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        data.charts = register_all_charts(data, plan)
        data.charts.update(hidden_charts(data, source_chart_styles(pdf, templates_of(cache))))
        # M8.5.2: every displayed table gets a chart block on its page; a split mirror box keeps the
        # rows that fit, and the charts are then registered again on the capped tables
        from src.rendering._placement import place_charts
        charted = {spec.table_id for spec in data.charts.values() if spec.display}
        placed, caps = place_charts(templates_of(cache), charted, data.display, style_of(cache))
        for table_id, rows in caps.items():
            data.display[table_id] = keep_rows(data.display[table_id], rows, data.charts, table_id)
        if caps:
            data.charts = register_all_charts(data, plan)
            data.charts.update(hidden_charts(data, source_chart_styles(pdf, templates_of(cache))))
        out = root / "charts"
        out.mkdir(parents=True, exist_ok=True)
        (out / "display").mkdir(exist_ok=True)
        for table_id, frame in data.display.items():
            frame.to_csv(out / "display" / f"{table_id}.csv", index=False)
        profile = style_of(cache)
        boxes = {}                                          # table_id -> (width, height) of its chart block
        for page in placed:
            for block in page.blocks:
                if block.kind == "chart":
                    key = block.table_id or f"rc_{block.block_id}"
                    boxes.setdefault(key, (block.bbox[2] - block.bbox[0], block.bbox[3] - block.bbox[1]))
        font, font_pt = _chart_font(profile, Path(cfg.artifacts_dir))
        from src.rendering._charts import chart_colours
        colors = chart_colours(profile.palette)
        pngs, mismatched = [], []
        for chart_id, spec in data.charts.items():
            width, height = boxes.get(spec.table_id, (302.0, 302.0 * 0.62))
            # every chart text uses the body Text Style size (E-29 Font Lock: never scaled)
            draw_svg(data, chart_id, out / f"{chart_id}.svg", colors, font, font_pt, width, height)
            pngs.append(draw_png(data, chart_id, out / f"{chart_id}.png", colors, font, font_pt, width, height))
            sidecar = write_sidecar(data, chart_id, out / f"{chart_id}.json")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            ok = (assert_chart_matches_table(payload["series"], data.display[spec.table_id],
                                             list(payload["series"]))
                  if spec.orientation == "columns" else bool(payload["series"]))
            if not ok:
                mismatched.append(chart_id)
        sheet = contact_sheet(pngs, out / "contact_charts.png")
        _release_fonts()                          # matplotlib keeps drawn fonts OPEN (Windows lock)
        displayed = {spec.table_id for spec in data.charts.values() if spec.display}
        # a table no chart can be drawn for holds no numbers at all (ticker codes, rating criteria)
        hidden_tables = {spec.table_id for spec in data.charts.values() if not spec.display}
        unchartable = sorted(table_id for table_id in data.display
                             if table_id not in displayed and table_id not in hidden_tables)
        chartable = sorted(displayed)
        return {"charts": len(data.charts),
                "templates": [page.model_dump(mode="json") for page in placed],
                "capped_rows": caps,
                "kinds": {spec.table_id: spec.kind for spec in data.charts.values()},
                "sidecars_match": not mismatched, "mismatched": mismatched,
                "every_displayed_table": all(table in displayed for table in chartable),
                "hidden": sorted(spec.table_id for spec in data.charts.values() if not spec.display),
                "unchartable": unchartable,
                "contact_sheet": str(sheet) if sheet else None}

    def plan_stage(cache):
        from src.narrative import plan as build_plan

        data, report = load_run_dataset(run_directory(), cfg)
        data = _prepared(data, root, cfg)
        data.allowed_periods = list((cache.get("prepare") or {}).get("allowed_periods") or data.allowed_periods)
        outline, bible = build_plan(data, templates_of(cache), document(), cfg, gateway("text"),
                                    domain=report.domain)
        return {"report_title": outline.report_title, "doc_type": outline.doc_type,
                "domain": outline.domain,
                "sections": [{"sid": section.section_id, "source_title": section.source_title,
                              "title": section.title, "goal": section.goal,
                              "blocks": len(section.block_ids), "facts": len(section.fact_keys)}
                             for section in outline.sections],
                "bible": {"entities": len(bible.entities),
                          "kinds": dict(Counter(entity.kind for entity in bible.entities)),
                          "dates": sorted({value for entity in bible.entities
                                           for key, value in entity.attrs.items()
                                           if key in {"year", "date"}})},
                "outline": [section.model_dump(mode="json") for section in outline.sections],
                "bible_entities": [entity.model_dump(mode="json") for entity in bible.entities]}

    def write_stage(cache):
        from src.contracts.report import BibleEntity, Outline, SectionPlan, StoryBible
        from src.llm import estimate_tokens
        from src.narrative._prompts import WRITER
        from src.narrative._writer import build_writer_payload, write_blocks

        data, _report = load_run_dataset(run_directory(), cfg)
        data = _prepared(data, root, cfg)
        data.allowed_periods = list((cache.get("prepare") or {}).get("allowed_periods") or data.allowed_periods)
        plan_output = cache["plan"]
        outline = Outline(report_title=plan_output["report_title"],
                          sections=[SectionPlan.model_validate(item) for item in plan_output["outline"]],
                          doc_type=plan_output.get("doc_type", ""), domain=plan_output.get("domain", ""))
        bible = StoryBible(version=1, entities=[BibleEntity.model_validate(item)
                                                for item in plan_output["bible_entities"]])
        pages = templates_of(cache)
        blocks = {block.block_id: block for page in pages for block in page.blocks}
        avoid = avoid_list() or None
        if dry_run:
            from src.core.originality import tone_profile
            tone = tone_profile([b.text for b in blocks.values() if b.kind == "text" and b.text])
            cap = max(200, WRITER.max_input_tokens - estimate_tokens(WRITER.system))
            rows = []
            for section in outline.sections:
                full, _ = build_writer_payload(section, outline, bible, data.facts, list(data.allowed_periods),
                                               10 ** 6, blocks, tone=tone, avoid=avoid,
                                               dt=outline.doc_type, dom=outline.domain)
                tokens = estimate_tokens(full)
                fields = json.loads(full)
                rows.append({"sid": section.section_id, "tokens": tokens, "cap": cap, "over_cap": tokens > cap,
                             "has_dt_dom_tone_avoid": all(k in fields for k in ("dt", "dom", "tone", "avoid"))})
            return {"dry_run": True, "cap": cap, "sections": rows}
        doc = write_blocks(data, outline, bible, pages, None, cfg, gateway("text"), avoid=avoid,
                           dt=outline.doc_type, dom=outline.domain)
        return {"words": doc.word_count, "llm_used": doc.llm_used, "flags": doc.flags,
                "claims": doc.claims, "new_entities": doc.new_entities,
                "conflicts": doc.conflicts, "unknown_refs": doc.unknown_refs,
                "resolved": doc.resolved, "dropped": doc.dropped,
                "conflicts_initial": doc.conflicts,
                "repeats": getattr(doc, "repeats", {}),          # v3.3 F-11
                # conflicts the Reconciler could not resolve; their sentences were dropped (E-08)
                "unresolved_dropped": max(0, doc.conflicts - doc.resolved),
                "final_conflicts": 0,
                "blocks": doc.blocks,
                "sections": [{"sid": section.section_id, "title": section.title, "text": section.text,
                              "words": len(section.text.split()),
                              "ok": not any(flag.startswith(f"{section.section_id}: fallback")
                                            for flag in doc.flags),
                              "flags": [flag for flag in doc.flags if flag.startswith(section.section_id)]}
                             for section in doc.sections]}

    def fit_stage(cache):
        from src.rendering._fit_doc import fit_blocks
        from src.rendering._measure import box_for, width_fn_for

        profile = style_of(cache)
        blocks = {block.block_id: block for page in templates_of(cache) for block in page.blocks}
        write_out = cache["write"]
        texts = dict(write_out.get("blocks") or {})
        periods = list((cache.get("prepare") or {}).get("allowed_periods") or [])
        boxes = {block_id: box_for(blocks[block_id], profile) for block_id in texts if block_id in blocks}
        widths = {block_id: width_fn_for(blocks[block_id], profile, Path(cfg.artifacts_dir)) for block_id in boxes}
        texts, rows, summary = fit_blocks(texts, boxes, widths, cfg, gateway("text"), periods)
        outline = (cache.get("plan") or {}).get("outline") or []
        owner = {block_id: item.get("section_id") for item in outline for block_id in item.get("block_ids", [])}
        sections = []
        for section in write_out.get("sections", []):
            ids = [block_id for block_id in texts if owner.get(block_id) == section["sid"]]
            text = "\n\n".join(texts[block_id] for block_id in ids) if ids else section.get("text", "")
            sections.append({**section, "text": text, "words": len(text.split())})
        return {"blocks": texts, "sections": sections,
                "words": sum(len(section["text"].split()) for section in sections),
                "flags": list(write_out.get("flags") or []) + summary["flags"],
                "rows": rows, "overflow_before": summary["overflow_before"],
                "shortened": summary["shortened"], "cut": summary["cut"],
                "all_fit": all(row.get("fits", True) for row in rows)}

    def images_stage(cache):
        from src.rendering._images import logo_slots, photo_slots, run_images

        fit = cache["fit"]
        pages = placed_of(cache)
        figures_by_id = {item.get("figure_id"): item for item in (cache.get("figures") or {}).get("items", [])}
        photos = photo_slots(pages, figures_by_id, dict(fit.get("blocks") or {}))
        logos = logo_slots(pages)
        # E-25: the document type named by the Titles call; never a fixed type
        doc_type = (cache.get("plan") or {}).get("doc_type") or "document"
        result = run_images(photos, logos, cfg, style_of(cache), doc_type, gateway("image"), gateway("vision"),
                            root / "images", pdf)
        captions = {item["slot"]["caption_block_id"]: item["caption"] for item in result["items"]
                    if item["slot"].get("caption_block_id") and item["caption"]}
        merged = dict(fit.get("blocks") or {})
        merged.update(captions)
        result["blocks"] = merged
        for item in result["items"]:                 # image bytes live in the saved JPEGs, not the JSON
            item.pop("image", None)
        result["captions"] = [{"slot": item["slot"]["index"], "block_id": item["slot"].get("caption_block_id"),
                               "caption": item["caption"]} for item in result["items"]]
        if not photos:
            result.update({"photos": 0, "skipped": True})
        return result

    def meta_of(cache) -> dict:
        organisation = document().entity_name or ""                 # E-32: the organisation is the author
        if "lang" not in state:                                     # v3.3 F-10: detected once per document
            from src.rendering._css import detect_language
            state["lang"] = detect_language(" ".join(document().text_blocks))
        return {"title": (cache.get("plan") or {}).get("report_title") or organisation, "author": organisation,
                "lang": state["lang"]}

    def render_stage(cache):
        from src.rendering._pages import render_pages
        pages = placed_of(cache)
        texts = page_texts(cache, pages, cfg, (document().entity_name or "").strip(), style_of(cache),
                           _display_tables(root))
        charts_by_table, _ = _chart_files(root)
        return render_pages(pages, texts, style_of(cache), _display_tables(root), charts_by_table,
                            _image_files(cache), root / "render", Path(cfg.artifacts_dir), pdf, meta_of(cache))

    def qa_stage(cache):
        import shutil

        from src.rendering._pages import render_pages
        from src.rendering.qa import qa as run_qa

        pages = placed_of(cache)
        profile = style_of(cache)
        tables = _display_tables(root)
        charts_by_table, sidecars = _chart_files(root)
        texts = page_texts(cache, pages, cfg, (document().entity_name or "").strip(), style_of(cache),
                           _display_tables(root))
        images = _image_files(cache)
        meta = meta_of(cache)
        source_pages = sorted((root / "render" / "pages").glob("_source_p*.png"),
                              key=lambda path: int(path.stem.split("_p")[-1]))

        def render_round(adjustments=None):
            result = render_pages(pages, texts, profile, tables, charts_by_table, images,
                                  root / "render" / "critic" / f"r{len(state.setdefault('rounds', []))}",
                                  Path(cfg.artifacts_dir), pdf, meta, adjustments=adjustments or {})
            state["rounds"].append(result["pdf"])
            return result["pdf"], [Path(item) for item in result["pngs"]]

        vision = gateway("vision")
        report = run_qa(cache["render"]["pdf"], pages, profile, tables, sidecars, set(avoid_list()), pdf, cfg,
                        vision, render_round if vision is not None else None, source_pages,
                        set((cache.get("charts") or {}).get("unchartable", [])))
        final = root / "report_output.pdf"
        shutil.copy(report.pdf_path, final)
        # v3.3: round trip (F-08), layout metrics and discriminator (F-09), chart style (F-06)
        from src.report_checks import v33_output_checks
        kept_pages = sorted(Path(report.pdf_path).parent.joinpath("pages").glob("p*.png"),
                            key=lambda path: int(path.stem[1:]) if path.stem[1:].isdigit() else 0)
        kept_pages = [p for p in kept_pages if p.stem[1:].isdigit()]
        extra = v33_output_checks(pdf, final, pages, profile, cfg, vision, source_pages, kept_pages)
        layout_flags = [f"p{item['page']}: layout model agreement {item['agreement']} < "
                        f"{cfg.style.layout_min_agreement}" for item in (cache.get("templates") or {}).get(
                            "layout_flagged", [])]
        gates = {**report.gates, **extra["gates"], "layout_agreement": True}   # F-05 flags, never fails (E-48)
        return {"gates": gates, "failures": report.failures + extra["failures"],
                "round_scores": report.round_scores, "kept_round": report.kept_round,
                "critic_ran": vision is not None,
                "manual_review": report.manual_review_required + extra["manual_review"] + layout_flags,
                "roundtrip": extra.get("roundtrip"), "iou": extra.get("iou"),
                "text_placement": extra.get("text_placement"),
                "alignment": extra.get("alignment"), "overlap": extra.get("overlap"),
                "chart_style": extra.get("chart_style"), "discriminator": extra.get("discriminator"),
                "pdf": str(final)}

    return [Stage("style", style), Stage("templates", templates), Stage("figures", figures),
            Stage("prepare", prepare), Stage("charts", charts), Stage("plan", plan_stage),
            Stage("write", write_stage), Stage("fit", fit_stage), Stage("images", images_stage),
            Stage("render", render_stage), Stage("qa", qa_stage)]
