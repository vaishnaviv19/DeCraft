# src/contracts/report.py  -- contracts added in v3.1 (generation-to-report). Additive only (SCHEMA_VERSION 2.1).
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BBox = tuple[float, float, float, float]          # x0, y0, x1, y1 in PDF points, origin top-left


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- style and page templates (M8.1-M8.3)
class FontSpec(Strict):
    font_id: str                       # 'F1'
    source_name: str                   # e.g. 'ABCDEF+Calibri-Bold'
    family: str                        # cleaned family name, e.g. 'Calibri'
    bold: bool = False
    italic: bool = False
    file: str | None = None            # extracted font file (relative path) when reusable
    fallback: str                      # open font used when file is None, e.g. 'Carlito'


class TextStyle(Strict):
    style_id: str                      # 'T3'
    font_id: str
    size_pt: float
    color: str                         # '#1F3864'
    line_height: float = 1.2           # multiple of size
    align: Literal["left", "right", "center", "justify"] = "left"


class StyleProfile(Strict):
    page_width_pt: float
    page_height_pt: float
    margins_pt: tuple[float, float, float, float]      # top, right, bottom, left
    fonts: list[FontSpec]
    text_styles: list[TextStyle]
    palette: list[str] = Field(min_length=1)           # hex colours, most used first
    table_border_pt: float = 0.5
    table_header_fill: str | None = None
    header_text: str | None = None                     # running header (e.g. entity name)
    footer_text: str | None = None


BlockKind = Literal["heading", "text", "table", "chart", "photo", "logo", "caption", "header", "footer",
                    "page_number", "decoration"]


class Block(Strict):
    block_id: str                      # 'p3b07'
    kind: BlockKind
    bbox: BBox
    text: str | None = None            # the block's text (headings, paragraphs, captions)
    style_id: str | None = None        # TextStyle for text-like blocks
    source_words: int = 0              # words in the SOURCE block (word budget = +/-15%)
    section_id: str | None = None      # which section owns this block
    table_id: str | None = None        # for table/chart blocks
    figure_id: str | None = None       # for chart/photo blocks
    bullet: str | None = None          # E-59: the list bullet the source printed before the text ('❑')
    indent_pt: float = 0.0             # ... and the hanging indent from the bullet to the text


class PageTemplate(Strict):
    page: int = Field(ge=1)
    mode: Literal["mirror", "flow"]    # 'flow' when mirroring is unsafe (decision E-15)
    reason: str = ""
    blocks: list[Block]
    # E-53: an added chart page has no source page of its own; its masthead, logo and footer are copied
    # from this source page. None = the page IS source page `page`.
    source_page: int | None = None


# ---------------------------------------------------------------- source figures (Style/Chart Analyst)
ChartKind = Literal["bar", "horizontal_bar", "stacked_bar", "line", "area", "pie", "donut", "scatter", "other"]


class FigureInfo(Strict):
    figure_id: str
    kind: Literal["chart", "photo", "logo", "icon", "other"]
    chart_kind: ChartKind | None = None
    has_legend: bool = False
    has_data_labels: bool = False
    description: str = ""              # photos only: subject, setting, composition, mood (no identities)


class RecoveredSeries(Strict):
    name: str
    values: list[float]


class RecoveredChart(Strict):
    figure_id: str
    categories: list[str]
    series: list[RecoveredSeries]
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)


# ---------------------------------------------------------------- outline, story bible, drafts (M8.5-M8.8)
class SectionPlan(Strict):
    section_id: str                    # 'S1'
    source_title: str
    title: str                         # new title (decision E-11: same sections, new titles)
    goal: str                          # one sentence: what this section must convey
    block_ids: list[str]               # text/heading/caption blocks owned by the section
    fact_keys: list[str]               # facts this section may cite
    table_ids: list[str] = []


class Outline(Strict):
    report_title: str
    sections: list[SectionPlan]
    doc_type: str = ""                 # 'dt' (E-25): named by the Titles call from page 1, never assumed
    domain: str = ""                   # 'dom' (E-25): the Annotator's domain from the Inference Report


class BibleEntity(Strict):
    id: str                            # 'E1'
    kind: Literal["product", "event", "person_role", "initiative", "place", "org_unit", "award", "partner"]
    name: str
    attrs: dict[str, str] = {}         # e.g. {'year': 'FY25', 'location': 'Pune'}; years must be allowed periods


class StoryBible(Strict):
    version: int = 1
    entities: list[BibleEntity]


class BlockText(Strict):
    id: str                            # block_id
    text: str                          # paragraphs separated by a blank line; numbers only as {{placeholders}}


class ClaimOut(Strict):
    e: str                             # entity id (bible id or a new id 'N1')
    a: str                             # attribute
    v: str                             # value
    p: int = Field(ge=0)               # paragraph index inside the block
    b: str                             # block id


class NewEntityOut(Strict):
    id: str                            # 'N1', 'N2' ... (temporary; Python renames to 'N:<section>:<k>')
    kind: Literal["product", "event", "person_role", "initiative", "place", "org_unit", "award", "partner"]
    name: str
    attrs: dict[str, str] = {}


class WriterOut(Strict):
    blocks: list[BlockText]
    claims: list[ClaimOut] = []
    new: list[NewEntityOut] = []


class Rewrite(Strict):
    section_id: str
    block_id: str
    paragraph: int = Field(ge=0)
    text: str


class ReconcileOut(Strict):
    choice: str                        # MUST be one of the candidate values
    reason: str
    rewrites: list[Rewrite]


class ShortenOut(Strict):
    text: str


class TitlesOut(Strict):
    report_title: str
    sections: list[dict[str, str]]     # [{'id': 'S1', 'title': '...', 'goal': '...'}]


class LabelOut(Strict):
    col: str                           # 'segments.Segment'
    src: str                           # source label
    new: str                           # fictional replacement (equal to src only for generic vocabulary)


class LabelPattern(Strict):
    col: str                           # a column with too many labels to list one by one
    pattern: str                       # '{first} {last}'; the slot {digits} is filled by Python
    parts: dict[str, list[str]] = {}


class LabelsOut(Strict):
    labels: list[LabelOut]             # E-27: company-specific category labels -> fictional ones
    patterns: list[LabelPattern] = []  # E-35: large identifying columns (merged fake-value agent)


class BibleOut(Strict):
    entities: list[BibleEntity]


# ---------------------------------------------------------------- visual QA (M8.11-M8.12)
class CriticIssue(Strict):
    block_id: str | None = None
    problem: str
    # E-29: no font-size levers; sizes always equal the source TextStyles (challenge task 8)
    param: Literal["line_height", "paragraph_spacing", "chart_width", "chart_height", "legend_position",
                   "table_row_padding", "col_width_ratio", "none"]
    target: str | None = None          # style_id / block_id the change applies to
    value: float | str | None = None


class CriticOut(Strict):
    score: int = Field(ge=0, le=10)    # 10 = indistinguishable in layout and style
    issues: list[CriticIssue] = []


class DiscriminatorOut(Strict):
    """v3.3 F-09: which of two pages the vision model takes for the published original."""
    original: Literal["A", "B"]
    reason: str


class ImageCheckOut(Strict):
    matches: bool
    missing: list[str] = []            # caption elements not visible in the image


class QAReport(Strict):
    gates: dict[str, bool] = {}
    failures: list[str] = []
    round_scores: list[list[int]] = []
    kept_round: int = 0
    manual_review_required: list[str] = []
    pdf_path: str = ""
