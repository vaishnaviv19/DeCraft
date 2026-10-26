# src/style/_prompts.py   -- vision prompts used on the SOURCE PDF
from src.llm import PromptSpec

FIGURES = PromptSpec(
    name="figure_analyst", version="v1", role="vision", temperature=0.0, max_input_tokens=2600,
    max_output_tokens=700, reasoning_effort=None,
    rationale="Deterministic labels of source figures; one call per page that has figures.",
    system=("You see one page of a report and a list of figure boxes (id + bbox in points, origin top-left). For each "
            "figure return its kind (chart, photo, logo, icon, other); for charts the chart_kind (bar, "
            "horizontal_bar, stacked_bar, line, area, pie, donut, scatter, other), has_legend and has_data_labels; "
            "for photos a short description of subject, setting, composition and mood without identifying any "
            "person. Return ONLY JSON: "
            '{"figures":[{"figure_id":str,"kind":str,"chart_kind":str|null,"has_legend":bool,'
            '"has_data_labels":bool,"description":str}]}'))

CHART_DATA = PromptSpec(
    name="chart_data_recovery", version="v1", role="vision", temperature=0.0, max_input_tokens=1800,
    max_output_tokens=700, reasoning_effort=None,
    rationale="Recovers series only for charts with no table behind them; values are inputs to inference, "
              "never printed.",
    system=("Read the chart image. Return the category labels and each series' values as numbers in the chart's "
            "own unit, plus the unit and your confidence 0-1. If values are not readable, estimate from the axis "
            "and lower the confidence. Return ONLY JSON: "
            '{"figure_id":str,"categories":[str],"series":[{"name":str,"values":[number]}],"unit":str|null,'
            '"confidence":number}'))

ALL = [FIGURES, CHART_DATA]
