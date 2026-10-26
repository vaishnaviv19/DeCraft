"""Run DeCraft on a Source PDF: python scripts/run_decraft.py [pdf] [--llm]."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# OpenBLAS allocates one worker per core by default and can fail on a small Windows machine that is
# already close to its memory limit ("Memory allocation still failed after 10 retries"). These values
# must be set before numpy is imported. The pipeline's numeric work is tiny, so one thread is enough.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.llm import make_gateways  # noqa: E402
from src.pipeline import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeCraft on one Source PDF.")
    parser.add_argument("pdf", nargs="?", default=str(ROOT / "data" / "input" / "sample_input_1.pdf"))
    llm = parser.add_mutually_exclusive_group()
    llm.add_argument("--llm", action="store_true", help="enable model calls (default: off)")
    llm.add_argument("--no-llm", action="store_true", help="force the deterministic path (M9.2)")
    parser.add_argument("--artifacts", default=None, help="artifacts directory (default from config)")
    parser.add_argument("--fresh", action="store_true", help="ignore every completed Stage")
    parser.add_argument("--until", default=None, help="stop after this Stage")
    args = parser.parse_args()

    overrides = {"llm_enabled": bool(args.llm) and not args.no_llm}
    if args.artifacts:
        overrides["artifacts_dir"] = args.artifacts
    cfg = load_config(**overrides)
    gateways = None
    if args.llm:
        gateways = make_gateways(cfg)
        provider = getattr(gateways.get("text"), "provider", None)
        print(f"[run] text model: {provider or 'none (set GROQ_API_KEY or start Ollama) - deterministic fallbacks'}")
    result = run(Path(args.pdf), cfg, gateways=gateways, fresh=args.fresh, until=args.until)
    print(f"[run] {result.run_id} status={result.status}")
    print(f"[run] directory: {result.run_dir}")
    print(f"[run] pdf: {result.pdf_path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
