# tests/rendering/test_critic.py
from pathlib import Path

from src.config import load_config
from src.rendering._critic import CriticRaw, clean_issues, param_specs, run_critic

CFG = load_config(llm_enabled=False)
SPECS = param_specs(CFG)


class FakeVision:
    enabled = True

    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []

    def ask_json(self, prompt, user, schema, images=None):
        self.calls.append({"name": prompt.name, "images": images or []})
        return schema.model_validate({"score": self.scores.pop(0), "issues": []})


def pngs(tmp_path, count=2):
    paths = []
    for index in range(count):
        path = tmp_path / f"p{index}.png"
        path.write_bytes(b"image")
        paths.append(path)
    return paths


def test_numeric_values_are_clamped_and_unknown_params_are_ignored():
    raw = CriticRaw(score=5, issues=[
        {"param": "line_height", "value": 99, "problem": "too tight"},
        {"param": "table_row_padding", "value": -4, "problem": "too much"},
        {"param": "font_size", "value": 12, "problem": "not allowed"},
        {"param": "legend_position", "value": "somewhere", "problem": "not an option"}])
    cleaned = clean_issues(raw, SPECS)
    assert len(cleaned) == 2
    assert cleaned[0]["param"] == "line_height" and cleaned[0]["value"] == SPECS["line_height"]["max"]
    assert cleaned[1]["value"] == SPECS["table_row_padding"]["min"]


def test_line_height_has_a_ten_percent_range_and_the_others_twenty():
    line = SPECS["line_height"]
    assert round(line["max"] / line["value"], 2) == 1.1
    padding = SPECS["table_row_padding"]
    assert round(padding["max"] / padding["value"], 2) == 1.2


def test_a_round_that_lowers_the_mean_score_is_not_kept(tmp_path):
    pages = pngs(tmp_path)
    render_calls = []

    def render_round(adjustments):
        render_calls.append(dict(adjustments))
        return f"pdf-{len(render_calls)}", pages

    vision = FakeVision([8, 8, 5, 5, 7, 7])
    result = run_critic(render_round, [], SPECS, CFG, vision)
    assert result["round_scores"] == [[8, 8], [5, 5], [7, 7]]
    assert result["kept_round"] == 0
    assert result["pdf"] == "pdf-1"


def test_the_best_round_is_kept_and_at_most_three_passes_run(tmp_path):
    pages = pngs(tmp_path)
    render_calls = []

    def render_round(adjustments):
        render_calls.append(dict(adjustments))
        return f"pdf-{len(render_calls)}", pages

    vision = FakeVision([6, 6, 7, 7, 9, 9])
    result = run_critic(render_round, [], SPECS, CFG, vision)
    assert len(render_calls) == 3 and len(result["round_scores"]) == 3
    assert result["kept_round"] == 2 and result["pdf"] == "pdf-3"


def test_no_critic_round_when_the_config_allows_none(tmp_path):
    cfg = CFG.model_copy(update={"report": CFG.report.model_copy(update={"critic_rounds": 0})})
    render_calls = []

    def render_round(adjustments):
        render_calls.append(adjustments)
        return "pdf-1", pngs(tmp_path)

    result = run_critic(render_round, [], SPECS, cfg, FakeVision([7, 7]))
    assert len(render_calls) == 1 and result["kept_round"] == 0
