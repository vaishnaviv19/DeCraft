# src/pipeline.py  -- orchestrates every Stage into one Run Directory
"""Run the whole pipeline on one Source PDF.

Stages: extract -> infer -> privacy plan -> generate -> privacy audit -> write -> render.
Each Stage has a timeout and a token budget; its completion is persisted in `run_state.json`, so a
rerun with the same source and Config resumes at the first unfinished Stage. A failed Run still writes
its Run Directory and Run Manifest.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import platform
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from src.blueprint.build_blueprint import build_blueprint
from src.extraction import extract_structured
from src.config import Config
from src.contracts import (InferenceReport, PrivacyPlan, RunManifest, RunResult, SCHEMA_VERSION)
from src.contracts.dataset import CanonicalDataset
from src.errors import DeCraftError, LLMBudgetExceeded, StageTimeout
from src.generation import generate, register_charts
from src.inference import infer
from src.llm import LLMGateway
from src.narrative import write
from src.planner.build_plan import build_document_plan
from src.privacy import audit_privacy, plan_privacy
from src.rendering import render
from src.validation import validate

REPO_ROOT = Path(__file__).resolve().parents[1]

STAGES = ("extract", "infer", "privacy_plan", "generate", "privacy_audit", "write", "render")
PACKAGES = ("pydantic", "pandas", "numpy", "scipy", "networkx", "pyyaml", "pymupdf", "jinja2",
            "weasyprint", "matplotlib", "faker")


class _Run:
    """Small helper around one Run Directory: logging, state and artifact paths."""

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.state_path = run_dir / "run_state.json"
        self.log_path = run_dir / "logs.jsonl"
        self.state: dict[str, Any] = {"stages": {}}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.state.setdefault("stages", {})
            except json.JSONDecodeError:
                self.state = {"stages": {}}

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def log(self, event: str, **fields: Any) -> None:
        entry = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def stage(self, name: str, fn: Callable[[], Any], timeout: float) -> Any:
        """Run one Stage, persist completion, enforce the timeout without hanging on a dead thread."""
        started = time.monotonic()
        executor = cf.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fn)
        try:
            result = future.result(timeout=timeout)
        except cf.TimeoutError as error:
            self.log("stage_timeout", stage=name, timeout=timeout)
            raise StageTimeout(f"{name} exceeded {timeout:.0f}s") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        self.state["stages"][name] = "done"
        self.save_state()
        self.log("stage_done", stage=name, seconds=round(time.monotonic() - started, 2))
        return result

    def done(self, name: str) -> bool:
        return self.state["stages"].get(name) == "done"

    def json(self, name: str, value: Any) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _packages() -> dict[str, str]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            continue
    return found


def _manifest(run_id: str, source_sha: str, cfg: Config, status: str = "partial",
              started: datetime | None = None, finished: datetime | None = None,
              outputs: dict[str, str] | None = None, gates: dict[str, bool] | None = None,
              limitations: list[str] | None = None) -> RunManifest:
    return RunManifest(
        run_id=run_id, started_at=started or datetime.now(timezone.utc), finished_at=finished,
        status=status, source_sha256=source_sha, seed=cfg.seed, config_hash=cfg.config_hash(),
        config_snapshot=cfg.model_dump(mode="json"), python_version=platform.python_version(),
        packages=_packages(), llm={"enabled": cfg.llm_enabled, "prompt_versions": {}},
        outputs=outputs or {}, gates=gates or {}, limitations=limitations or [])


def _write_manifest(run: _Run, manifest: RunManifest) -> Path:
    return run.json("manifest.json", manifest)


def run(pdf_path: Path | str, cfg: Config, gateway: LLMGateway | None = None) -> RunResult:
    """Orchestrate one Run. Always writes the Run Directory and Manifest, also on failure."""
    pdf_path = Path(pdf_path)
    started = datetime.now(timezone.utc)
    source_sha = _sha256(pdf_path) if pdf_path.is_file() else ""
    run_id = (f"{source_sha[:12]}-{cfg.config_hash()[:8]}" if source_sha
              else f"failed-{started.strftime('%Y%m%dT%H%M%S')}")
    run_dir = Path(cfg.artifacts_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run = _Run(run_dir)
    manifest = _manifest(run_id, source_sha, cfg, started=started)
    _write_manifest(run, manifest)
    run.log("run_start", run_id=run_id, llm=cfg.llm_enabled, resumed=bool(run.state["stages"]))
    fallbacks: list[str] = []

    if not pdf_path.is_file():
        _write_manifest(run, _manifest(run_id, "", cfg, status="failed", started=started,
                                       finished=datetime.now(timezone.utc)))
        raise FileNotFoundError(f"Source PDF not found: {pdf_path}")

    run.json("config.json", cfg.model_dump(mode="json"))

    try:
        blueprint = _load_blueprint(run)
        if blueprint is None:
            blueprint, plan = run.stage("extract", lambda: _extract(pdf_path, run, cfg),
                                        cfg.extraction.timeout_seconds)
        else:
            plan = _load_plan(run)
            if plan is None:
                plan = build_document_plan(blueprint, out_dir=run.dir / "outputs" / "plan")
        run.json("blueprint.json", blueprint)
        run.json("document_plan.json", plan)

        doc = _source_document(blueprint, source_sha)
        if not cfg.llm_enabled:
            run.log("fallback", stage="narrative", reason="llm disabled")

        def fresh_budget(stage: str) -> None:
            if gateway is not None and cfg.llm_enabled:
                gateway.start_stage(stage)             # every stage has its own token budget

        report = _load_model(run, "infer", "inference.json", InferenceReport)
        if report is None:
            fresh_budget("infer")
            try:
                report = run.stage("infer",
                                   lambda: infer(doc, cfg, gateway if cfg.llm_enabled else None),
                                   cfg.budgets.stage_timeout_seconds)
            except LLMBudgetExceeded:
                run.log("fallback", stage="infer", reason="token budget")
                fallbacks.append("infer: token budget")
                report = run.stage("infer", lambda: infer(doc, cfg, None),
                                   cfg.budgets.stage_timeout_seconds)
            run.json("inference.json", report)
        if not cfg.llm_enabled:
            report.llm_used = False

        privacy_plan = _load_model(run, "privacy_plan", "privacy_plan.json", PrivacyPlan)
        if privacy_plan is None:
            fresh_budget("privacy_plan")
            try:
                privacy_plan = run.stage(
                    "privacy_plan", lambda: plan_privacy(doc, report, cfg, gateway if cfg.llm_enabled else None),
                    cfg.budgets.stage_timeout_seconds)
            except LLMBudgetExceeded:
                run.log("fallback", stage="privacy_plan", reason="token budget")
                fallbacks.append("privacy_plan: token budget")
                privacy_plan = run.stage("privacy_plan", lambda: plan_privacy(doc, report, cfg),
                                         cfg.budgets.stage_timeout_seconds)
            run.json("privacy_plan.json", privacy_plan)
            run.json("avoid.json", privacy_plan.avoid)            # E-28: source proper nouns
            run.log("labels", columns=len(privacy_plan.label_maps),
                    replaced=sum(len(m) for m in privacy_plan.label_maps.values()),
                    kept=sum(len(k) for k in privacy_plan.kept_labels.values()), avoid_size=len(privacy_plan.avoid))

        data = run.stage("generate", lambda: _with_charts(generate(doc, report, privacy_plan, cfg, _rng(cfg)),
                                                          plan), cfg.budgets.stage_timeout_seconds)
        _write_dataset(run, data)
        run.json("validation_report.json", data.validation)

        privacy_report = run.stage("privacy_audit", lambda: audit_privacy(doc, data, privacy_plan, cfg),
                                   cfg.budgets.stage_timeout_seconds)
        run.json("privacy_report.json", privacy_report)

        fresh_budget("write")
        narrative = run.stage("write", lambda: _write(run, data, plan, cfg, gateway),
                              cfg.budgets.stage_timeout_seconds)
        run.json("narrative.json", narrative)

        result = run.stage("render", lambda: render(data, narrative, blueprint, cfg, run_dir / "render",
                                                    plan=plan, doc=doc),
                           cfg.budgets.stage_timeout_seconds)

        qa = (result.qa or {}).get("rendered_pdf_qa", {})
        run.json("rendered_pdf_qa.json", qa)
        run.json("utility_report.json", {
            "warnings": [v.model_dump(mode="json") for v in data.validation.violations
                         if v.check.startswith("utility_")]})
        checks = qa.get("checks", {})
        gates = {
            "validation": bool(data.validation.ok),
            "rendered_pdf_qa": bool(qa.get("ok")),
            "word_count": bool(checks.get("word_count")),
            "no_placeholders": bool(checks.get("no_unresolved_placeholders")),
            "chart_table": bool(checks.get("chart_sidecars") and checks.get("charts_captioned")),
            "privacy_audit": bool(privacy_report.ok),
        }
        limitations = list(privacy_report.limitations)
        limitations.append("Hosted model versions, library versions and installed fonts can change "
                           "the rendered output.")
        if not gates["rendered_pdf_qa"]:
            limitations.append("Rendered-PDF QA did not pass every check; see rendered_pdf_qa.")
        status = "ok" if all(gates.values()) and result.pdf_path else "failed"

        outputs = _hash_outputs(run_dir)
        finished = datetime.now(timezone.utc)
        manifest = _manifest(run_id, source_sha, cfg, status=status, started=started,
                             finished=finished, outputs=outputs, gates=gates, limitations=limitations)
        manifest.llm["fallbacks"] = fallbacks
        manifest_path = _write_manifest(run, manifest)
        run.json("result.json", {"run_id": run_id, "status": status, "pdf_path": result.pdf_path,
                                 "gates": gates})
        run.log("run_end", status=status, gates=gates)
        return RunResult(run_id=run_id, run_dir=str(run_dir), status=status,
                         manifest_path=str(manifest_path), pdf_path=result.pdf_path)
    except Exception as error:
        run.log("run_error", error=f"{type(error).__name__}: {error}")
        _write_manifest(run, _manifest(run_id, source_sha, cfg, status="failed", started=started,
                                       finished=datetime.now(timezone.utc),
                                       limitations=[f"Run failed: {type(error).__name__}"]))
        raise


def _write(run: _Run, data, plan, cfg, gateway):
    """Section writing with the Fallback Ladder: a spent budget drops to the deterministic templates."""
    if not cfg.llm_enabled or gateway is None:
        return write(data, plan, cfg, None)
    try:
        return write(data, plan, cfg, gateway)
    except LLMBudgetExceeded:
        run.log("fallback", stage="write", reason="token budget")
        return write(data, plan, cfg, None)


def _with_charts(data: CanonicalDataset, plan: dict) -> CanonicalDataset:
    """Every chart the Document Plan pairs with a table becomes a registered ChartSpec."""
    data.charts = register_charts(data, plan)
    return data


def _cache_dir(cfg: Config) -> Path:
    path = Path(cfg.extraction.cache_dir)
    return path if path.is_absolute() else REPO_ROOT / path


def _extract(pdf_path: Path, run: _Run, cfg: Config):
    """E-37: parse THIS PDF into the run folder, then run the frozen blueprint and planner code there
    (never the repo's pre-extracted outputs/structured, never the repo's outputs/ folders)."""
    summary = extract_structured(pdf_path, run.dir, cache_dir=_cache_dir(cfg), method=cfg.extraction.method,
                                 models_dir=cfg.extraction.models_dir)
    run.log("extraction", method=summary["method"], cached=summary["cached"], tables=summary["table_count"],
            figures=len(summary.get("saved_figures", [])), warnings=summary.get("warnings", []))
    blueprint = build_blueprint(pdf_path, project_root=run.dir)
    plan = build_document_plan(blueprint, out_dir=run.dir / "outputs" / "plan")
    run.json("blueprint.json", blueprint)
    run.json("document_plan.json", plan)
    return blueprint, plan


def _load_blueprint(run: _Run) -> dict | None:
    if not run.done("extract"):
        return None
    path = run.dir / "blueprint.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_plan(run: _Run) -> dict | None:
    path = run.dir / "document_plan.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_model(run: _Run, stage: str, name: str, kind):
    if not run.done(stage):
        return None
    path = run.dir / name
    if not path.exists():
        return None
    try:
        return kind.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        run.state["stages"].pop(stage, None)
        run.save_state()
        return None


def _source_document(blueprint: dict, source_sha: str):
    from src.blueprint import to_source_document
    return to_source_document(blueprint, document_sha256=source_sha)


def _rng(cfg: Config):
    import numpy as np
    return np.random.default_rng(cfg.seed)


def _write_dataset(run: _Run, data: CanonicalDataset) -> None:
    tables = run.dir / "canonical_dataset"
    display = run.dir / "display"
    tables.mkdir(exist_ok=True)
    display.mkdir(exist_ok=True)
    for table_id, frame in data.tables.items():
        frame.to_csv(tables / f"{table_id}.csv", index=False)
    for table_id, frame in data.display.items():
        frame.to_csv(display / f"{table_id}.csv", index=False)
    run.json("facts.json", {key: fact.model_dump(mode="json") for key, fact in data.facts.items()})
    run.json("lineage.json", data.lineage)
    run.json("charts.json", {key: spec.model_dump(mode="json") for key, spec in data.charts.items()})


def _hash_outputs(run_dir: Path) -> dict[str, str]:
    outputs = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.relative_to(run_dir).as_posix()] = _sha256(path)
    return outputs
