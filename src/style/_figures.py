# src/style/_figures.py
"""Figure analysis (M8.3): Figure Analyst labels and data recovered from charts with no table.

One vision call per page that holds figures (E-03); rule fallback when the call fails or an id is
missing. A chart is lone when its section has no table; its data is recovered at confidence >= 0.4 and
only then becomes a hidden source table (E-19). A chart is never drawn from invented data.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config import Config
from src.contracts.report import FigureInfo, PageTemplate, RecoveredChart, RecoveredSeries
from src.llm import LLMGateway
from src.style._prompts import CHART_DATA, FIGURES

MAX_PX = 1024
MIN_CONFIDENCE = 0.4
FIGURE_KINDS = {"chart", "photo", "logo", "icon", "other"}
# kinds the vision model uses that are not in the prompt's list (live run 2026-10-26: "table")
KIND_ALIASES = {"table": "other", "diagram": "other", "image": "photo", "graph": "chart"}

log = logging.getLogger("decraft.style")


class FigureOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    figure_id: str
    kind: str
    chart_kind: str | None = None
    has_legend: bool = False
    has_data_labels: bool = False
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _nulls_mean_default(cls, data):
        # Llama 4 Scout answers null for "no description" / "unknown legend" (live check 2026-10-26)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None or k == "chart_kind"}
        return data


class FiguresOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    figures: list[FigureOut]


class SeriesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    values: list[float]


class ChartOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    figure_id: str
    categories: list[str]
    series: list[SeriesOut]
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)


def _jpeg(page, clip=None, dpi: int = 110) -> bytes:
    pixmap = page.get_pixmap(dpi=dpi, clip=clip)
    image = pixmap.tobytes("png")
    from PIL import Image
    img = Image.open(io.BytesIO(image)).convert("RGB")
    scale = min(1.0, MAX_PX / max(img.size))
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()


def fallback_label(block_kind: str) -> tuple[str, str | None]:
    """Rule fallback (section 4): photo, logo, or a chart drawn with bars."""
    if block_kind == "logo":
        return "logo", None
    if block_kind == "chart":
        return "chart", "bar"
    return "photo", None


MIN_GEOMETRY_CONFIDENCE = 0.9         # v3.3 F-07 (style.chart_recovery_min_conf)


def analyse_figures(pdf_path: Path, templates: list[PageTemplate], cfg: Config,
                    gateway: LLMGateway | None) -> tuple[list[FigureInfo], list[RecoveredChart]]:
    """Label every source figure and recover data for the lone charts."""
    figures: list[FigureInfo] = []
    recovered: list[RecoveredChart] = []
    with pymupdf.open(str(pdf_path)) as pdf:
        for template in templates:
            blocks = [b for b in template.blocks if b.kind in {"photo", "logo", "chart"}]
            if not blocks:
                continue
            page = pdf[template.page - 1]
            answered: dict[str, FigureOut] = {}

            if gateway is not None and gateway.enabled:
                payload = {"figures": [{"id": b.block_id, "bbox": [round(v, 1) for v in b.bbox]}
                                       for b in blocks]}
                reply = gateway.ask_json(FIGURES, json.dumps(payload), FiguresOut,
                                        images=[_jpeg(page)])
                if reply is not None:
                    for item in reply.figures:
                        if item.figure_id in answered:
                            log.warning("figure_analyst answered %s twice; keeping the first",
                                        item.figure_id)
                            continue
                        if item.kind in KIND_ALIASES:
                            item = item.model_copy(update={"kind": KIND_ALIASES[item.kind]})
                        if item.kind not in FIGURE_KINDS:
                            log.warning("figure_analyst gave %s an unknown kind %r",
                                        item.figure_id, item.kind)
                            continue
                        answered[item.figure_id] = item
                for block in blocks:
                    if block.block_id not in answered:
                        log.warning("figure_analyst did not answer %s; using the rule fallback",
                                    block.block_id)

            for block in blocks:
                label = answered.get(block.block_id)
                if label is None:
                    kind, chart_kind = fallback_label(block.kind)
                    figures.append(FigureInfo(figure_id=block.block_id, kind=kind,
                                              chart_kind=chart_kind))
                else:
                    figures.append(FigureInfo(
                        figure_id=block.block_id, kind=label.kind,   # type: ignore[arg-type]
                        chart_kind=label.chart_kind,                 # type: ignore[arg-type]
                        has_legend=label.has_legend, has_data_labels=label.has_data_labels,
                        description=label.description))

            # Lone charts: a chart block not linked to a table on its page (style.link_charts). v3.3 F-07:
            # a vector chart's data is read from its geometry first (exact, no model call); the vision
            # model is only asked when that fails (raster charts, unlabelled or log axes).
            from src.style._chart_geometry import recover_chart
            for block in blocks:
                figure = next(f for f in figures if f.figure_id == block.block_id)
                if figure.kind != "chart" or block.table_id:
                    continue
                chart = (recover_chart(page, block.block_id, block.bbox, MIN_GEOMETRY_CONFIDENCE)
                         if block.kind == "chart" else None)          # vector chart: exact geometry
                if chart is not None:
                    recovered.append(chart)
                    continue
                chart = _recover_chart(page, block.block_id, block.bbox, gateway)                     if gateway is not None and gateway.enabled else None
                if chart is None and block.kind == "chart":
                    # nothing readable: a stand-in series in the chart's own frame (E-60), never an empty box
                    from src.style._chart_geometry import synthesize_chart
                    chart = synthesize_chart(page, block.block_id, block.bbox, cfg.seed)
                    if chart is not None:
                        log.warning("chart %s: data not readable; drawn with an invented series", block.block_id)
                if chart is None:
                    log.warning("chart %s could not be recovered; flagged for QA", block.block_id)
                else:
                    recovered.append(chart)
    return figures, recovered


def _recover_chart(page, figure_id: str, bbox, gateway: LLMGateway) -> RecoveredChart | None:
    clip = pymupdf.Rect(*bbox)
    payload = {"figure_id": figure_id}
    reply = gateway.ask_json(CHART_DATA, json.dumps(payload), ChartOut,
                             images=[_jpeg(page, clip=clip, dpi=150)])
    if reply is None or reply.figure_id != figure_id:
        return None
    if not reply.categories or not reply.series:
        return None
    if any(len(series.values) != len(reply.categories) for series in reply.series):
        log.warning("chart %s: series lengths differ from the categories; rejected", figure_id)
        return None
    if reply.confidence < MIN_CONFIDENCE:
        log.warning("chart %s: confidence %.2f below %.2f; rejected",
                    figure_id, reply.confidence, MIN_CONFIDENCE)
        return None
    return RecoveredChart(figure_id=figure_id, categories=list(reply.categories),
                          series=[RecoveredSeries(name=s.name, values=list(s.values))
                                  for s in reply.series],
                          unit=reply.unit, confidence=reply.confidence)


from src.core.recovered import recovered_to_source_table  # noqa: E402,F401  (re-exported)
