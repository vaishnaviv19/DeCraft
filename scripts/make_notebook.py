"""Write solution.ipynb (M10.2): a thin notebook; every code cell calls public interfaces or reads the Run
Directory, so the logic stays in src/.   python scripts/make_notebook.py"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip().splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip().splitlines(keepends=True)}


CELLS = [
    md("""
# DeCraft: a look-alike document with fabricated content

**Challenge.** From any input PDF, extract its layout, structure and visual style, then produce a new PDF for
the same organisation and branding whose sections, text, tables and charts are entirely fabricated: every
table has a chart with the same numbers, at least 1,000 words, the same fonts, borders and page layout.

**Architecture.**

```text
extract -> infer -> privacy_plan -> generate -> privacy_audit                (data: v3 M1-M7)
style -> templates -> figures -> prepare -> charts                           (look and data for the report)
plan -> write -> fit -> images -> render -> qa -> package                    (agents, rendering, gates)
```

**How to run.** Keys come only from environment variables (`GROQ_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`).
Without keys, set `DECRAFT_CACHE_ONLY=1` to replay the shipped model cache, or the notebook runs the
deterministic path (no model calls). All paths are relative to the project folder.
"""),
    code("""
import json, os, sys
from pathlib import Path
ROOT = Path.cwd() if (Path.cwd() / "src").exists() else Path.cwd().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLBACKEND", "Agg")
from src.config import load_config

PDF = Path("data/input/sample_input_1.pdf")
keys = all(os.environ.get(k) for k in ("GROQ_API_KEY", "CF_ACCOUNT_ID", "CF_API_TOKEN"))
cache_only = os.environ.get("DECRAFT_CACHE_ONLY") == "1"
LLM = keys or cache_only
print("keys found" if keys else ("cache-only mode" if cache_only else "no keys: deterministic path"))
cfg = load_config(llm_enabled=LLM)
print("seed", cfg.seed, "| config", cfg.config_hash()[:12])
"""),
    md("""
## Extraction, inference, privacy and generation
One call runs every Stage. Each Stage writes its outputs and a `done.json` under `artifacts/runs/<run id>/`, so
a rerun reuses finished Stages (and model replies come from the cache).
"""),
    code("""
from src.pipeline import run
from src.llm import make_gateways
result = run(ROOT / PDF, cfg, gateways=make_gateways(cfg) if LLM else None)
RUN = Path(result.run_dir)
print(result.status, RUN.name)
"""),
    code("""
import pandas as pd
inference = json.loads((RUN / "inference.json").read_text(encoding="utf-8"))
print("domain:", inference.get("domain"), "| tables profiled:", len({p["table_id"] for p in inference["profiles"]}))
pd.DataFrame([{"rule": r["kind"], "table": r["table_id"], "enforcement": r["enforcement"]} for r in inference["rules"]]
             ).value_counts().rename("count").reset_index().head(15)
"""),
    code("""
privacy = json.loads((RUN / "privacy_report.json").read_text(encoding="utf-8"))
print("privacy audit ok:", privacy["ok"], "| checks:", ", ".join(privacy["checks_run"]))
pd.DataFrame(privacy["violations"])[["check", "table_id", "message"]] if privacy["violations"] else "no violations"
"""),
    md("## Style and page templates\nFonts, text styles and palette come from the source PDF; every page becomes a template of positioned blocks (E-48: the source layout is kept)."),
    code("""
from IPython.display import HTML, Image, display
style = json.loads((RUN / "style" / "output.json").read_text(encoding="utf-8"))
display(HTML("".join(f"<span style='display:inline-block;width:46px;height:22px;background:{c};margin:2px'></span>" for c in style["palette"])))
display(pd.DataFrame(style["fonts"])[["font_id", "family", "bold", "italic", "fallback"]])
display(Image(filename=str(RUN / "templates" / "overlay_p1.png"), width=520))
"""),
    md("## Figures and charts\nEvery displayed table gets a chart drawn from the same display table; the sidecar of each chart is checked against its table."),
    code("""
charts = json.loads((RUN / "charts" / "output.json").read_text(encoding="utf-8"))
print("charts:", charts["charts"], "| sidecars match:", charts["sidecars_match"], "| recovered source charts:", charts["hidden"])
display(Image(filename=charts["contact_sheet"], width=760)) if charts.get("contact_sheet") else None
pd.DataFrame([{"table": t, "chart": k} for t, k in charts["kinds"].items()])
"""),
    md("## Planning\nNew section titles (same sections, new wording) and the story bible every writer shares."),
    code("""
plan = json.loads((RUN / "plan" / "output.json").read_text(encoding="utf-8"))
print("document type:", plan.get("doc_type") or "-", "| domain:", plan.get("domain") or "-")
display(pd.DataFrame(plan["sections"])[["sid", "source_title", "title", "goal"]])
pd.DataFrame(plan["bible_entities"])[["id", "kind", "name"]]
"""),
    md("## Writing and consistency\nOne writer per section; claims are merged, conflicts reconciled (at most 2 rounds), repeats across sections removed."),
    code("""
write = json.loads((RUN / "write" / "output.json").read_text(encoding="utf-8"))
print({k: write.get(k) for k in ("words", "llm_used", "claims", "conflicts_initial", "resolved", "final_conflicts")})
print("repeats:", write.get("repeats"))
pd.DataFrame(write["sections"])[["sid", "title", "words", "ok"]]
"""),
    md("## Images"),
    code("""
images = json.loads((RUN / "images" / "output.json").read_text(encoding="utf-8"))
print("photo slots:", images.get("slots", 0), "| generated:", images.get("generated", 0), "| logos copied:", len(images.get("logos", [])))
"""),
    md("## Render and QA\nSource page | output page, the gates, and the critic scores per round."),
    code("""
qa = json.loads((RUN / "qa" / "output.json").read_text(encoding="utf-8"))
for png in sorted((RUN / "render" / "pages").glob("side_by_side_p*.png")):
    display(Image(filename=str(png), width=900))
gates = json.loads((RUN / "gates.json").read_text(encoding="utf-8")) if (RUN / "gates.json").exists() else {"gates": qa["gates"], "notes": {}}
display(pd.DataFrame([{"gate": g, "ok": v, "note": gates["notes"].get(g, "")} for g, v in gates["gates"].items()]))
print("critic scores per round:", qa.get("round_scores"), "| manual review:", qa.get("manual_review"))
"""),
    md("## Deliverables"),
    code("""
import subprocess
print("report:", result.pdf_path, "| words:", write.get("words"))
print(subprocess.run([sys.executable, "scripts/build_prompt_log.py"], cwd=ROOT, capture_output=True, text=True).stdout[-400:])
pkg = json.loads((RUN / "package" / "output.json").read_text(encoding="utf-8")) if (RUN / "package" / "output.json").exists() else {}
print("submission zip:", pkg.get("zip"), "| entries:", pkg.get("entries"))
"""),
    md("## Generality (E-22)\nThe same pipeline on a PDF of a different document type, with no special-casing in the code."),
    code("""
SECOND = ROOT / "data/input/second_input.pdf"
if SECOND.exists():
    second = run(SECOND, cfg, gateways=make_gateways(cfg) if LLM else None)
    g2 = json.loads((Path(second.run_dir) / "gates.json").read_text(encoding="utf-8"))
    display(pd.DataFrame([{"gate": g, "ok": v} for g, v in g2["gates"].items()]))
else:
    print("second_input.pdf not added yet (M11.2)")
"""),
]


def main() -> int:
    notebook = {"cells": CELLS, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                                             "name": "python3"},
                                            "language_info": {"name": "python"}},
                "nbformat": 4, "nbformat_minor": 5}
    (ROOT / "solution.ipynb").write_text(json.dumps(notebook, indent=1), encoding="utf-8")
    print("wrote solution.ipynb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
