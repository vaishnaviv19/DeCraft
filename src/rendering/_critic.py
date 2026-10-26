# src/rendering/_critic.py
"""Visual Critic loop (M8.12.2, E-18).

One `visual_critic` call per page with the source and output page images and the allow-listed
parameters (current value and range). Python clamps every proposed value, ignores parameters outside
the list, aggregates the accepted changes and re-renders. Initial plus at most `report.critic_rounds`
fixes; the round with the highest mean score is kept. Unresolved issues go to manual review."""
from __future__ import annotations

import io
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.contracts.report import CriticOut
from src.rendering._prompts import CRITIC

NUMERIC = {"line_height": 1.2, "paragraph_spacing": 2.0, "chart_width": 1.0, "chart_height": 1.0,
           "table_row_padding": 1.0, "col_width_ratio": 1.0}
LEGEND = ["best", "upper right", "upper left", "lower right", "lower left"]


class CriticRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int
    issues: list[dict] = []


def param_specs(cfg) -> dict:
    """Current value and allowed range for every parameter the critic may touch."""
    span = cfg.report.critic_param_range
    specs: dict[str, dict] = {}
    for param, value in NUMERIC.items():
        width = span * (0.5 if param == "line_height" else 1.0)     # E-18: line height +/-10%
        specs[param] = {"value": value, "min": round(value * (1 - width), 3),
                        "max": round(value * (1 + width), 3)}
    specs["legend_position"] = {"value": "best", "options": LEGEND}
    return specs


def clean_issues(raw: CriticRaw, specs: dict) -> list[dict]:
    """Keep allow-listed parameters only and clamp numeric values into their range."""
    cleaned = []
    for issue in raw.issues:
        param = issue.get("param")
        if param not in specs:
            continue
        value = issue.get("value")
        spec = specs[param]
        if "min" in spec and isinstance(value, (int, float)):
            value = min(spec["max"], max(spec["min"], float(value)))
        elif "options" in spec:
            if value not in spec["options"]:
                continue
        cleaned.append({"problem": issue.get("problem", ""), "param": param,
                        "target": issue.get("target"), "value": value,
                        "block_id": issue.get("block_id")})
    return cleaned


def aggregate(issues: list[dict]) -> dict[str, dict[str, float | str]]:
    out: dict[str, dict[str, float | str]] = {}
    for issue in issues:
        param, target = issue["param"], str(issue.get("target") or "*")
        out.setdefault(param, {})[target] = issue.get("value")
    return out


def _merge(base: dict, extra: dict) -> dict:
    merged = {param: dict(values) for param, values in base.items()}
    for param, values in extra.items():
        merged.setdefault(param, {}).update(values)
    return merged


def _jpeg(path) -> bytes:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=80)
            return buffer.getvalue()
    except Exception:
        return Path(path).read_bytes()


def _pair(left: bytes, right: bytes) -> bytes:
    """Cloudflare llama-3.2-11b-vision accepts ONE image per request (two give HTTP 400, measured
    2026-10-26), so two pages go side by side in one JPEG: left = first, right = second."""
    from PIL import Image
    a, b = (Image.open(io.BytesIO(x)).convert("RGB") for x in (left, right))
    height = max(a.height, b.height)
    canvas = Image.new("RGB", (a.width + b.width + 16, height), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 16, 0))
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def run_critic(render_round, source_pages: list[Path], specs: dict, cfg, vision) -> dict:
    """render_round(adjustments) -> (pdf_path, [page pngs]). Returns scores, kept round and PDF."""
    adjustments: dict = {}
    scores_rounds: list[list[int]] = []
    best = None
    best_issues: list[dict] = []
    for round_number in range(1 + max(0, cfg.report.critic_rounds)):
        pdf, pngs = render_round(adjustments)
        scores, issues = [], []
        for index, png in enumerate(pngs):
            images = ([_pair(_jpeg(source_pages[index]), _jpeg(png))] if index < len(source_pages)
                      else [_jpeg(png)])
            payload = {"page": index + 1, "params": specs}
            reply = vision.ask_json(CRITIC, json.dumps(payload), CriticRaw, images=images)
            if reply is None:
                continue
            scores.append(max(0, min(10, int(reply.score))))
            issues.extend(clean_issues(reply, specs))
        scores_rounds.append(scores)
        mean = sum(scores) / len(scores) if scores else 0.0
        if best is None or mean > best["mean"]:
            best = {"round": round_number, "pdf": pdf, "mean": mean, "scores": scores}
            best_issues = issues
        if round_number < cfg.report.critic_rounds:
            adjustments = _merge(adjustments, aggregate(issues))
    manual = [issue["problem"] for issue in best_issues if issue.get("param") == "none"]
    return {"round_scores": scores_rounds, "kept_round": best["round"],
            "manual_review": manual, "pdf": best["pdf"]}
