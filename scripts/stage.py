"""Run report stages for development: python scripts/stage.py --pdf PDF --until style|templates|...

Each stage writes artifacts/runs/dev/<stage>/output.json and done.json; a rerun reuses a stage whose
inputs have not changed. `--fresh` re-runs everything.

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
from src.stage_runner import STAGE_NAMES, Stage, run_stages  # noqa: E402


def _document():
    from src.blueprint import to_source_document
    blueprint_path = ROOT / "outputs" / "blueprint" / "blueprint.json"
    if not blueprint_path.exists():
        raise FileNotFoundError(
            f"{blueprint_path} is missing; run scripts/run_blueprint_and_plan.py first")
    return to_source_document(json.loads(blueprint_path.read_text(encoding="utf-8")))


def _not_yet(stage: str, milestone: str):
    def run(_cache):
        raise NotImplementedError(f"stage {stage!r} lands in {milestone}")
    return run


def build_stages(pdf: Path, cfg) -> list[Stage]:
    from src.style import build_templates, extract_style

    def style(_cache):
        try:
            return extract_style(pdf, cfg).model_dump(mode="json")
        except NotImplementedError:
            raise NotImplementedError("stage 'style' lands in M8.1") from None

    def templates(cache):
        try:
            from src.contracts.report import StyleProfile
            style_value = StyleProfile.model_validate(cache["style"])
            return [page.model_dump(mode="json")
                    for page in build_templates(pdf, _document(), style_value, cfg)]
        except NotImplementedError:
            raise NotImplementedError("stage 'templates' lands in M8.2") from None

    return [
        Stage("style", style),
        Stage("templates", templates),
        Stage("figures", _not_yet("figures", "M8.3")),
        Stage("prepare", _not_yet("prepare", "M8.4")),
        Stage("charts", _not_yet("charts", "M8.5")),
        Stage("plan", _not_yet("plan", "M8.6")),
        Stage("write", _not_yet("write", "M8.7")),
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
    args = parser.parse_args()
    stages = build_stages(Path(args.pdf), load_config(llm_enabled=False))
    run_stages(stages, args.until, Path(args.dir), fresh=args.fresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
