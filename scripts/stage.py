"""Run report stages for development: python scripts/stage.py --pdf PDF --until style|templates|...

Each stage writes <dir>/<stage>/output.json and done.json (default dir: artifacts/runs/dev), and every
file it makes goes under that folder too; a rerun reuses a stage whose inputs, config and PDF have not
changed. `--fresh` re-runs everything. The generation run (blueprint, dataset) used is the newest one
made from the `--pdf` file (E-37), never another PDF's run. The stages themselves live in
`src/report_stages.py` and are the same ones `pipeline.run` uses.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.report_stages import build_report_stages, latest_run  # noqa: E402
from src.stage_runner import STAGE_NAMES, Stage, run_stages, stage_salt  # noqa: E402


def build_stages(pdf: Path, cfg, root: Path | None = None, dry_run: bool = False,
                 run_dir: Path | None = None) -> list[Stage]:
    root = Path(root) if root is not None else ROOT / "artifacts" / "runs" / "dev"
    return build_report_stages(pdf, cfg, root, run_dir=run_dir, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeCraft report stages up to one stage.")
    parser.add_argument("--pdf", default=str(ROOT / "data" / "input" / "sample_input_1.pdf"))
    parser.add_argument("--until", required=True, choices=STAGE_NAMES)
    parser.add_argument("--dir", default=str(ROOT / "artifacts" / "runs" / "dev"))
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--llm", action="store_true", help="use the provider gateways when keys exist")
    parser.add_argument("--no-llm", action="store_true", help="force the deterministic path")
    parser.add_argument("--dry-run", action="store_true", help="write stage: payload sizes only")
    args = parser.parse_args()
    pdf = Path(args.pdf)
    cfg = load_config(llm_enabled=args.llm and not args.no_llm)
    run_dir = latest_run(cfg, pdf)
    salt = stage_salt(cfg, pdf, run_dir.name, "dry-run" if args.dry_run else "")
    ocr = run_dir / "source_ocr.pdf"                    # v3.3 F-04: the run's OCR'd copy, when it made one
    stages = build_stages(ocr if ocr.exists() else pdf, cfg, Path(args.dir), dry_run=args.dry_run,
                          run_dir=run_dir)                # found by the ORIGINAL file's sha256
    run_stages(stages, args.until, Path(args.dir), fresh=args.fresh, salt=salt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
