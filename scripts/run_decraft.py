"""Run DeCraft on a Source PDF: python scripts/run_decraft.py [pdf] [--llm]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.pipeline import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeCraft on one Source PDF.")
    parser.add_argument("pdf", nargs="?", default=str(ROOT / "data" / "input" / "sample_input_1.pdf"))
    parser.add_argument("--llm", action="store_true", help="enable model calls (default: off)")
    parser.add_argument("--artifacts", default=None, help="artifacts directory (default from config)")
    args = parser.parse_args()

    overrides = {"llm_enabled": bool(args.llm)}
    if args.artifacts:
        overrides["artifacts_dir"] = args.artifacts
    cfg = load_config(**overrides)
    result = run(Path(args.pdf), cfg)
    print(f"[run] {result.run_id} status={result.status}")
    print(f"[run] directory: {result.run_dir}")
    print(f"[run] pdf: {result.pdf_path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
