# scripts/build_prompt_log.py  -- render PROMPT_LOG.md (M10.1, E-24)
"""Build `PROMPT_LOG.md` from the live `PromptSpec`s, the call log and the human notes.

Sections (M10.1): overview/architecture, agents, full prompts, real payload examples, prompt
iterations with failure counts, libraries, rules, decisions, external data and limitations.

The builder is importable (`build_prompt_log`) so tests can give it a fixture JSONL and notes file.
`--check` exits non-zero when the generated file differs from the one on disk.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.prompt_registry import all_prompts  # noqa: E402

VISION_PROMPTS = {"visual_critic", "image_check", "figure_analyst", "chart_data_recovery"}
LIBRARY_WHY = {
    "pymupdf": "read the source PDF, extract text/vector/font/image data and render PDFs for QA",
    "pypdf": "read page metadata and count pages in the rendered PDF",
    "jsonschema": "validate the JSON examples in the contracts",
    "scikit-learn": "numeric utilities for the pattern/relationship checks",
    "Pillow": "encode page and photo images as JPEG for the vision models",
    "numpy": "deterministic numeric generation and statistics",
    "pandas": "canonical dataset, display table and CSV round-trips",
    "docling": "fallback table extraction when a page has no vector table",
    "rich": "readable terminal progress and stage summaries",
    "pydantic": "strict contracts at every Stage boundary",
    "networkx": "relationship graph and dependency ordering",
    "scipy": "trend/break statistics (Mann-Kendall, BIC)",
    "pyyaml": "the one Config file",
    "python-dateutil": "period parsing and shifting",
    "openpyxl": "write the workbook copy of the canonical dataset",
    "jinja2": "HTML page templates",
    "weasyprint": "render the styled HTML pages to PDF with embedded fonts",
    "matplotlib": "data-consistent SVG charts",
    "faker": "deterministic believable replacement values",
    "httpx": "hosted model and image API calls",
    "requests": "local Ollama HTTP calls",
    "pytest": "test suite",
    "rapidocr_onnxruntime": "v3.3 F-04: OCR of scanned pages (ONNX, CPU)",
    "doclayout-yolo": "v3.3 F-05: layout-model second opinion on block classification (CPU)",
    "langdetect": "v3.3 F-10: the source language, for hyphenation",
    "pyphen": "v3.3 F-10: hyphenation dictionaries used by WeasyPrint",
    "fonttools": "text measurement with real glyph widths (box fitting, E-14)",
    "nbconvert": "executes solution.ipynb (M10.2)",
    "pytest-cov": "coverage report",
}

ARCHITECTURE = """```text
PDF -> extraction -> blueprint + document plan -> inference -> privacy plan -> generation
    -> style -> page templates -> figure analysis -> prepare -> charts -> narrative plan
    -> writer -> fit -> images -> render (HTML -> PDF) -> QA/critic -> package
```"""


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


def _installed(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return ""


def _config() -> dict:
    import yaml
    path = ROOT / "config" / "default.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}


def _requirements(project_root: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    text = _read(project_root / "requirements.txt")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)\s*([<>=!~].*)?$", line)
        if not match:
            continue
        name, spec = match.group(1), (match.group(2) or "").strip()
        actual = _installed(name) or spec.lstrip("<>=!~")
        rows.append((name, actual or "not installed", LIBRARY_WHY.get(name, "project dependency")))
    return rows


def _load_log(log_path: Path) -> list[dict]:
    records = []
    for line in _read(log_path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _stats(records: list[dict]) -> dict[tuple[str, str], Counter]:
    stats: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for record in records:
        key = (str(record.get("prompt", "")), str(record.get("version", "")))
        stats[key]["calls"] += 1
        if record.get("ok") is False:
            stats[key]["failures"] += 1
        if record.get("cache_hit"):
            stats[key]["cache_hits"] += 1
    return stats


def _all_versions(directory: Path, name: str) -> list[str]:
    versions = []
    for path in directory.glob(f"{name}_v*.txt"):
        match = re.search(r"_v(\d+)\.txt$", path.name)
        if match:
            versions.append(f"v{match.group(1)}")
    return sorted(set(versions), key=lambda value: int(value[1:]))


def _payloads(cache_dir: Path) -> dict[str, str]:
    """One real payload per prompt when the gateway saved it with the cached reply."""
    found: dict[str, str] = {}
    if not cache_dir.exists():
        return found
    for path in cache_dir.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "payload" not in data or "prompt" not in data:
            continue
        found.setdefault(str(data["prompt"]), str(data["payload"]))
    return found


def _notes_sections(notes: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    blocks = re.split(r"(?m)^##\s+", notes)
    for block in blocks[1:]:
        heading, _, body = block.partition("\n")
        for name in re.findall(r"[a-z][a-z0-9_]+", heading):
            sections.setdefault(name, (heading + "\n" + body).strip())
    return sections


def _decision_tables(project_root: Path) -> str:
    text = _read(project_root / "docs" / "DeCraft_Implementation_Spec_v3.md")
    text += "\n" + _read(project_root / "docs" / "project_decisions.md")
    spec = project_root.parent / "DeCraft_Spec_v3.2_Addendum.md"
    text += "\n" + _read(spec)
    lines = []
    current = ""
    for line in text.splitlines():
        if re.match(r"^\|\s*(ID|Task)\s*\|", line):
            current = line
            lines.append(line)
        elif current and line.startswith("|") and re.match(r"^\|\s*(D|B|G|E)-", line):
            lines.append(line)
        elif current and line.startswith("|") and re.match(r"^\|\s*\|", line):
            lines.append(line)
    return "\n".join(lines) if lines else "See `docs/DeCraft_Implementation_Spec_v3.md` and `docs/project_decisions.md`."


def render_prompt_log(prompts: dict, records: list[dict], notes: str, versions: dict[str, list[str]],
                      payloads: dict[str, str], libraries: list[tuple[str, str, str]],
                      decisions: str) -> str:
    cfg = _config()
    provider_text = (cfg.get("providers") or {}).get("text") or {}
    provider_vision = (cfg.get("providers") or {}).get("vision") or {}
    stats = _stats(records)
    notes_by_name = _notes_sections(notes)

    out: list[str] = ["# Prompt log", ""]
    out += ["## 1. System overview and architecture", "",
            "DeCraft reads one source PDF and writes a new, fully fabricated document with the same "
            "structure and visual style. Every Stage reads/writes files under `artifacts/runs/<run_id>/`; "
            "the model gateway is the only way to a model. Keys come only from environment variables.", "",
            ARCHITECTURE, ""]

    out += ["## 2. Agents", "",
            "| Agent | Version | Purpose | Provider | Model ID | Temperature | Reasoning effort | Max input | Max output | Why |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, prompt in prompts.items():
        vision = getattr(prompt, "role", "text") == "vision"     # the PromptSpec says where it is sent
        provider = "Cloudflare Workers AI" if vision else "Groq"
        model = (provider_vision if vision else provider_text).get("model", "")
        purpose = prompt.role
        out.append(f"| `{name}` | {prompt.version} | {purpose} | {provider} | `{model}` | "
                   f"{prompt.temperature} | {prompt.reasoning_effort} | {prompt.max_input_tokens} | "
                   f"{prompt.max_output_tokens} | {prompt.rationale} |")
    out.append("")

    out += ["## 3. Full system prompts", ""]
    for name, prompt in prompts.items():
        out += [f"### `{name}` {prompt.version}", "", "```text", prompt.system, "```", ""]

    out += ["## 4. Key user prompts (real cached payloads)", "",
            "Source data is allowed by E-02. Each example is truncated to 1,500 characters.", ""]
    for name in prompts:
        payload = payloads.get(name, "").replace("\r\n", "\n")
        if payload:
            out += [f"### `{name}`", "", "```json", payload[:1500], "```", ""]
        else:
            out += [f"### `{name}`", "", "_No cached payload captured yet._", ""]

    out += ["## 5. Prompt iterations", "",
            "Failure counts come from `artifacts/prompt_log.jsonl` (failed attempts; retries count).", ""]
    for name, prompt in prompts.items():
        out += [f"### `{name}`", "",
                "| Version | Calls | Failures | Cache hits | Note |",
                "| --- | --- | --- | --- | --- |"]
        for version_name in versions.get(name, [prompt.version]):
            counter = stats.get((name, version_name), Counter())
            note = notes_by_name.get(name, "").replace("\n", " ")[:240]
            out.append(f"| {version_name} | {counter['calls']} | {counter['failures']} | "
                       f"{counter['cache_hits']} | {note} |")
        out += ["", notes_by_name.get(name, "_No note yet._"), ""]

    out += ["## 6. Libraries", "", "| Library | Version | Why |", "| --- | --- | --- |"]
    for name, installed, why in libraries:
        out.append(f"| `{name}` | {installed} | {why} |")
    out.append("")

    out += ["## 7. Rules and skills", "",
            "* **Placeholders:** numbers enter prose only through `{{key...}}` placeholders; Python resolves "
            "them and rejects stray digits (E-10).",
            "* **Period tokens:** only shifted period labels and bible dates may contain digits in prose.",
            "* **Guards:** writers must keep source n-grams below 8 words, invent no entities, keep every "
            "placeholder, and return a claim for every factual sentence.",
            "* **Gates:** page count, placeholders, word totals and per-page word range, locked font sizes, "
            "no source proper names, embedded fonts, blocks inside boxes, layout fits, every table has a "
            "data-consistent chart, and empty manual-review list.",
            "* **Privacy:** source values are never kept for sensitive columns; labels are fabricated and "
            "verified by Python.",
            "* **Fonts:** sizes never change; only spacing, padding and box sizes may be adjusted.",
            ""]

    out += ["## 8. Architectural decisions", "", decisions, ""]

    out += ["## 9. Data sent to external APIs (E-02)", "",
            "* **Text prompts:** the source table samples, column headers/meanings, the section's own facts, "
            "the outline/story bible and cached page text needed by that agent.",
            "* **Vision prompts:** JPEG page images, source photo images and generated-photo candidates, with "
            "the caption/recovery question.",
            "* **Image generation:** the Python-built caption, composition and palette prompt sent to FLUX.",
            "* No key value is ever written to a prompt, a cache entry, `PROMPT_LOG.md` or the ZIP.",
            ""]

    out += ["## 10. Limitations and accepted risks", "",
            "* Hosted model IDs, free-tier limits and library behaviour can change; `requirements.txt` and "
            "the cache record what was tested.",
            "* A vision/critic miss is listed for manual review; the report never hides a failed gate.",
            "* Model knowledge of the real organisation cannot be fully detected; prompts forbid it and the "
            "source-name guard catches every proper noun present in the PDF.",
            "* Generated photos are not pixel-identical across runs (the FLUX endpoint rejects a seed); the "
            "saved images are reused on cache-only reruns.",
            "* The second-document generality run is reported separately in the M11 acceptance report.",
            ""]
    return "\n".join(out).rstrip() + "\n"


def build_prompt_log(project_root: Path | str = ROOT, log_path: Path | str | None = None,
                     notes_path: Path | str | None = None, out_path: Path | str | None = None,
                     prompts: dict | None = None, cache_dir: Path | str | None = None) -> Path:
    root = Path(project_root)
    log_path = Path(log_path) if log_path else root / "artifacts" / "prompt_log.jsonl"
    notes_path = Path(notes_path) if notes_path else root / "docs" / "prompt_changes.md"
    out_path = Path(out_path) if out_path else root / "PROMPT_LOG.md"
    prompt_dir = root / "docs" / "prompts"
    prompts = prompts or all_prompts()
    versions = {name: _all_versions(prompt_dir, name) or [prompt.version]
                for name, prompt in prompts.items()}
    records = _load_log(log_path)
    payloads = _payloads(Path(cache_dir) if cache_dir else root / "artifacts" / "llm_cache")
    text = render_prompt_log(prompts, records, _read(notes_path), versions, payloads,
                             _requirements(root), _decision_tables(root))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PROMPT_LOG.md")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--out", default=None)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args()
    out = Path(args.out) if args.out else Path(args.root) / "PROMPT_LOG.md"
    if args.check and out.exists():
        before = out.read_text(encoding="utf-8")
        build_prompt_log(args.root, out_path=out)
        after = out.read_text(encoding="utf-8")
        if before != after:
            print("[prompt-log] PROMPT_LOG.md is out of date", file=sys.stderr)
            return 1
        print("[prompt-log] up to date")
        return 0
    path = build_prompt_log(args.root, out_path=out)
    print(f"[prompt-log] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
