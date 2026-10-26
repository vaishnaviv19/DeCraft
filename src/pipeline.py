# src/pipeline.py  -- the v3.2 Run: generation half (v3) + report half (M8) as one Stage machine
"""M9: orchestrate every Stage of one Run, persist each Stage's completion, and resume.

Stage order (spec section 8, M9.1):

    extract -> infer -> privacy_plan -> generate -> privacy_audit
            -> style -> templates -> figures -> prepare -> charts
            -> plan -> write -> fit -> images -> render -> qa -> package

Every Stage reads earlier outputs from `artifacts/runs/<run_id>/<stage>/`, writes its own there, and
writes `done.json` with a hash of the inputs (source PDF, the parts of the Config that Stage uses, and
the output hashes of the earlier Stages). A rerun skips a Stage whose hash still matches; a changed
Config re-runs that Stage and everything after it, while earlier Stages are reused. The report Stages
themselves live in `src/report_stages.py` and are not re-implemented here.

`LLMBudgetExceeded` or `CacheMiss` stops the Run cleanly with status `partial` and a message naming the
Stage; the Manifest is still written, and the next Run resumes at that Stage (the gateway cache serves
the calls already made). Any other failure writes a `failed` Manifest and is re-raised.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from src.blueprint.build_blueprint import build_blueprint
from src.config import Config
from src.contracts import (InferenceReport, PrivacyPlan, RunManifest, RunResult, SCHEMA_VERSION)
from src.contracts.dataset import CanonicalDataset
from src.errors import CacheMiss, LLMBudgetExceeded, LLMBudgetExceeded as _Budget, StageTimeout
from src.extraction import extract_structured
from src.generation import generate, register_charts
from src.inference import infer
from src.llm import LLMGateway
from src.planner.build_plan import build_document_plan
from src.privacy import audit_privacy, plan_privacy
from src.report_stages import build_report_stages
from src.validation import validate

REPO_ROOT = Path(__file__).resolve().parents[1]

# The full v3.2 order. `build_report_stages` supplies the eleven report Stages in the middle.
PIPELINE_STAGES = ("extract", "infer", "privacy_plan", "generate", "privacy_audit", "style",
                   "templates", "figures", "prepare", "charts", "plan", "write", "fit", "images",
                   "render", "qa", "package")
PACKAGES = ("pydantic", "pandas", "numpy", "scipy", "networkx", "pyyaml", "pymupdf", "jinja2",
            "weasyprint", "matplotlib", "faker", "docling")

# What each Stage reads from the Config. A change re-runs that Stage and all later ones, never earlier
# ones. Report Stages are conservative because they read the Config directly.
STAGE_CONFIG_PARTS: dict[str, tuple[str, ...]] = {
    "extract": ("extraction", "seed"),
    "infer": ("thresholds", "budgets", "providers", "seed", "llm_enabled"),
    "privacy_plan": ("thresholds", "budgets", "providers", "seed", "llm_enabled"),
    "generate": ("thresholds", "report", "seed"),
    "privacy_audit": ("thresholds", "report", "seed"),
    "style": ("report", "seed"),
    "templates": ("report",),
    "figures": ("providers", "budgets", "thresholds", "llm_enabled"),
    "prepare": ("report", "seed", "thresholds", "providers", "budgets", "llm_enabled"),
    "charts": ("report",),
    "plan": ("report", "providers", "budgets", "thresholds", "llm_enabled"),
    "write": ("report", "providers", "budgets", "thresholds", "llm_enabled"),
    "fit": ("report", "providers", "budgets", "thresholds", "llm_enabled"),
    "images": ("report", "providers", "budgets", "llm_enabled"),
    "render": ("report",),
    "qa": ("report", "providers", "budgets", "thresholds", "llm_enabled"),
    "package": ("report",),
}


@dataclass
class PipelineStage:
    """One Stage of the Run: a name, the callable that does the work, and the Config parts it reads."""

    name: str
    run: Callable[[dict[str, Any]], Any]
    config_parts: tuple[str, ...] = ()
    timeout: float | None = None
    start: Callable[[], None] | None = None


class _PartialRun(Exception):
    """Internal: a Stage stopped on a spent quota or a cache-only miss; the Run is `partial`."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _packages() -> dict[str, str]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            continue
    return found


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
        """Run one Stage, enforce its timeout without hanging on a dead worker thread."""
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
        self.log("stage_done", stage=name, seconds=round(time.monotonic() - started, 2))
        return result

    def done(self, name: str) -> bool:
        return self.state["stages"].get(name) == "done"

    def json(self, name: str, value: Any) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_jsonable(value), indent=2, default=str), encoding="utf-8")
        return path


def _manifest(run_id: str, source_sha: str, cfg: Config, status: str = "partial",
              started: datetime | None = None, finished: datetime | None = None,
              outputs: dict[str, str] | None = None, gates: dict[str, bool] | None = None,
              limitations: list[str] | None = None, llm: dict[str, Any] | None = None) -> RunManifest:
    return RunManifest(
        run_id=run_id, started_at=started or datetime.now(timezone.utc), finished_at=finished,
        status=status, source_sha256=source_sha, seed=cfg.seed, config_hash=cfg.config_hash(),
        config_snapshot=cfg.model_dump(mode="json"), python_version=platform.python_version(),
        packages=_packages(), llm=llm or {"enabled": cfg.llm_enabled, "prompt_versions": {}},
        outputs=outputs or {}, gates=gates or {}, limitations=limitations or [])


def _write_manifest(run: _Run, manifest: RunManifest) -> Path:
    return run.json("manifest.json", manifest)


def _config_slice(cfg: Config, parts: tuple[str, ...]) -> Any:
    if not parts:
        return cfg.config_hash()
    dumped = cfg.model_dump(mode="json")
    return {part: dumped.get(part) for part in parts}


def _stage_hash(source_sha: str, cfg: Config, stage: PipelineStage,
                previous: list[str]) -> str:
    return _hash({"source_sha256": source_sha, "config": _config_slice(cfg, stage.config_parts),
                  "previous": previous})


def run_stages(stages: list[PipelineStage], root: Path, source_sha: str, cfg: Config,
               fresh: bool = False, until: str | None = None, log: Callable[..., None] | None = None,
               verbose: bool = False) -> dict[str, Any]:
    """Run the Stage machine. Returns {'executed', 'reused', 'output', 'values'}.

    This is the M9.1 contract, separated from the Stage implementations so it can be tested with fake
    Stages. Raises `_PartialRun` when a Stage hits `LLMBudgetExceeded` or `CacheMiss`.
    """
    names = [stage.name for stage in stages]
    if until is not None and until not in names:
        raise ValueError(f"unknown stage {until!r}; expected one of {names}")
    selected = stages[: names.index(until) + 1] if until else stages
    root = Path(root)
    values: dict[str, Any] = {}
    previous: list[str] = []
    executed: list[str] = []
    reused: list[str] = []
    for stage in selected:
        directory = root / stage.name
        output_path, done_path = directory / "output.json", directory / "done.json"
        input_hash = _stage_hash(source_sha, cfg, stage, previous)
        done: dict[str, Any] = {}
        if not fresh and done_path.exists() and output_path.exists():
            try:
                done = json.loads(done_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                done = {}
        if done.get("input_hash") == input_hash:
            try:
                value = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                done = {}
            else:
                values[stage.name] = value
                previous.append(done.get("output_hash") or _hash(value))
                reused.append(stage.name)
                if verbose and log:
                    log("stage_reused", stage=stage.name)
                continue
        started = time.monotonic()
        if log:
            log("stage_start", stage=stage.name)
        if stage.start is not None:
            stage.start()
        try:
            if stage.timeout:
                # never `with ThreadPoolExecutor`: its exit waits for a stuck worker, so a timed-out
                # Stage would hang the Run instead of failing it
                pool = cf.ThreadPoolExecutor(max_workers=1)
                try:
                    value = pool.submit(stage.run, dict(values)).result(timeout=stage.timeout)
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
            else:
                value = stage.run(dict(values))
        except (LLMBudgetExceeded, CacheMiss) as error:
            raise _PartialRun(stage.name, f"{stage.name}: {type(error).__name__}: {error}") from error
        except cf.TimeoutError as error:
            if log:
                log("stage_timeout", stage=stage.name, timeout=stage.timeout)
            raise StageTimeout(f"{stage.name} exceeded {stage.timeout:.0f}s") from error
        directory.mkdir(parents=True, exist_ok=True)
        value = _jsonable(value)
        output_hash = _hash(value)
        output_path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        done_path.write_text(json.dumps({"stage": stage.name, "input_hash": input_hash,
                                         "output_hash": output_hash}), encoding="utf-8")
        values[stage.name] = value
        previous.append(output_hash)
        executed.append(stage.name)
        if log:
            log("stage_done", stage=stage.name, seconds=round(time.monotonic() - started, 2))
    return {"until": until or (selected[-1].name if selected else None), "executed": executed,
            "reused": reused, "values": values,
            "output": values.get(selected[-1].name) if selected else None}


# ---------------------------------------------------------------- generation half (v3 Stages, wrapped)
def _compat_outputs(run: _Run, name: str, value: dict[str, Any]) -> None:
    """Keep the v3 Run Directory paths that existing clients and tests read."""
    if name == "write" and isinstance(value, dict):
        run.json("narrative.json", {"word_count": value.get("words", 0),
                                    "llm_used": value.get("llm_used", False),
                                    "flags": value.get("flags", []),
                                    "sections": value.get("sections", []),
                                    "blocks": value.get("blocks", {})})
    elif name == "qa" and isinstance(value, dict):
        run.json("rendered_pdf_qa.json", {"gates": value.get("gates", {}),
                                          "failures": value.get("failures", []),
                                          "round_scores": value.get("round_scores", []),
                                          "kept_round": value.get("kept_round", 0),
                                          "manual_review_required": value.get("manual_review", [])})
        # the deliverable at the repo root is written only by runs that use the project's own
        # artifacts folder; a test run (tmp artifacts) must never overwrite it
        pdf = value.get("pdf")
        if pdf and Path(pdf).exists() and _is_project_run(run):
            try:
                (REPO_ROOT / "report_output.pdf").write_bytes(Path(pdf).read_bytes())
            except OSError:
                pass


def _is_project_run(run: _Run) -> bool:
    try:
        run.dir.resolve().relative_to((REPO_ROOT / "artifacts").resolve())
        return True
    except ValueError:
        return False


def _ocr_source(pdf_path: Path, run: _Run, cfg: Config) -> Path:
    from src.style._ocr import ocr_copy
    target = run.dir / "source_ocr.pdf"
    try:
        info = ocr_copy(pdf_path, target, cfg.style.ocr_min_chars, cfg.style.ocr_min_image_cover,
                        cfg.style.ocr_min_confidence)
    except Exception as error:                       # OCR trouble never stops a Run on a text PDF
        run.log("ocr", error=f"{type(error).__name__}: {error}")
        return pdf_path
    run.json("ocr.json", info)
    if info["ocr_pages"]:
        run.log("ocr", pages=info["ocr_pages"], dropped_spans=info["dropped_spans"], engine=info["engine"],
                applied=info["applied"])
    return Path(info["path"])


def _build_stages(pdf: Path, cfg: Config, run: _Run,
                  gateways: dict[str, Any] | None, source_sha: str | None = None) -> list[PipelineStage]:
    source_sha = source_sha or _sha256(pdf)

    def extract(cache: dict[str, Any]) -> dict[str, Any]:
        blueprint, plan = _extract(pdf, run, cfg)
        return {"blueprint": blueprint, "plan": plan}

    def infer_stage(cache: dict[str, Any]) -> dict[str, Any]:
        blueprint = _read_json(run.dir / "blueprint.json") or {}
        doc = _source_document(blueprint, source_sha)
        report = infer(doc, cfg, gateways.get("text") if gateways and cfg.llm_enabled else None)
        run.json("inference.json", report)
        return report.model_dump(mode="json")

    def privacy_plan_stage(cache: dict[str, Any]) -> dict[str, Any]:
        doc = _document(run, source_sha)
        report = InferenceReport.model_validate_json(
            (run.dir / "inference.json").read_text(encoding="utf-8"))
        plan = plan_privacy(doc, report, cfg,
                            gateways.get("text") if gateways and cfg.llm_enabled else None)
        run.json("privacy_plan.json", plan)
        run.json("avoid.json", plan.avoid)
        run.log("labels", columns=len(plan.label_maps),
                replaced=sum(len(m) for m in plan.label_maps.values()),
                kept=sum(len(k) for k in plan.kept_labels.values()), avoid_size=len(plan.avoid))
        return plan.model_dump(mode="json")

    def generate_stage(cache: dict[str, Any]) -> dict[str, Any]:
        doc = _document(run, source_sha)
        report = InferenceReport.model_validate_json(
            (run.dir / "inference.json").read_text(encoding="utf-8"))
        plan = PrivacyPlan.model_validate_json(
            (run.dir / "privacy_plan.json").read_text(encoding="utf-8"))
        plan_doc = _read_json(run.dir / "document_plan.json") or None
        data = generate(doc, report, plan, cfg, _rng(cfg))
        data = _with_charts(data, plan_doc)
        _write_dataset(run, data)
        run.json("validation_report.json", data.validation)
        run.json("utility_report.json", {
            "warnings": [v.model_dump(mode="json") for v in data.validation.violations
                         if v.check.startswith("utility_")]})
        return {"tables": len(data.tables), "display": len(data.display),
                "facts": len(data.facts), "validation_ok": bool(data.validation.ok)}

    def privacy_audit_stage(cache: dict[str, Any]) -> dict[str, Any]:
        doc = _document(run, source_sha)
        data = _load_dataset(run, cfg)
        plan = PrivacyPlan.model_validate_json(
            (run.dir / "privacy_plan.json").read_text(encoding="utf-8"))
        report = audit_privacy(doc, data, plan, cfg)
        run.json("privacy_report.json", report)
        return report.model_dump(mode="json")

    def package_stage(cache: dict[str, Any]) -> dict[str, Any]:
        from scripts.package import build_submission

        qa_value = cache.get("qa") or {}
        report = qa_value.get("pdf") or str(run.dir / "report_output.pdf")
        out = run.dir / "submission.zip"
        path = build_submission(project_root=REPO_ROOT, out=out, run_dir=run.dir,
                                report_pdf=Path(report), optional=True)
        import zipfile
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        return {"zip": str(path), "entries": len(names), "relative": all(
            not name.startswith(("/", "\\")) and ":" not in name for name in names)}

    def start_stage(name: str) -> None:
        for client in (gateways or {}).values():
            if client is not None and hasattr(client, "start_stage"):
                client.start_stage(name)

    generation = [
        PipelineStage("extract", extract, STAGE_CONFIG_PARTS["extract"],
                      cfg.extraction.timeout_seconds, start=lambda: start_stage("extract")),
        PipelineStage("infer", infer_stage, STAGE_CONFIG_PARTS["infer"],
                      cfg.budgets.stage_timeout_seconds, start=lambda: start_stage("infer")),
        PipelineStage("privacy_plan", privacy_plan_stage, STAGE_CONFIG_PARTS["privacy_plan"],
                      cfg.budgets.stage_timeout_seconds, start=lambda: start_stage("privacy_plan")),
        PipelineStage("generate", generate_stage, STAGE_CONFIG_PARTS["generate"],
                      cfg.budgets.stage_timeout_seconds, start=lambda: start_stage("generate")),
        PipelineStage("privacy_audit", privacy_audit_stage, STAGE_CONFIG_PARTS["privacy_audit"],
                      cfg.budgets.stage_timeout_seconds, start=lambda: start_stage("privacy_audit")),
    ]
    report_stages = [PipelineStage(stage.name, _wrap_report_stage(run, stage),
                                   STAGE_CONFIG_PARTS.get(stage.name, ("report",)),
                                   cfg.budgets.stage_timeout_seconds,
                                   start=lambda name=stage.name: start_stage(name))
                     for stage in build_report_stages(pdf, cfg, root=run.dir, run_dir=run.dir,
                                                      gateways=gateways)]
    return generation + report_stages + [
        PipelineStage("package", package_stage, STAGE_CONFIG_PARTS["package"],
                      cfg.budgets.stage_timeout_seconds, start=lambda: start_stage("package"))]


def _wrap_report_stage(run: _Run, stage) -> Callable[[dict[str, Any]], Any]:
    def run_it(cache: dict[str, Any]) -> Any:
        value = stage.run(cache)
        if isinstance(value, dict):
            _compat_outputs(run, stage.name, value)
            for flag in value.get("flags", []) or []:
                run.log("fallback", stage=stage.name, reason=str(flag))
        return value
    return run_it


def run(pdf_path: Path | str, cfg: Config, gateway: LLMGateway | None = None,
        gateways: dict[str, Any] | None = None, fresh: bool = False,
        until: str | None = None) -> RunResult:
    """Orchestrate one Run. Always writes the Run Directory and Manifest, also on failure/partial."""
    pdf_path = Path(pdf_path)
    started = datetime.now(timezone.utc)
    source_sha = _sha256(pdf_path) if pdf_path.is_file() else ""
    # E-?/M9.1: the Run id is the source's identity only. The Config hash lives in every Stage's
    # done.json, so a changed Config reuses earlier Stages instead of starting a new Run directory.
    run_id = source_sha[:12] if source_sha else f"failed-{started.strftime('%Y%m%dT%H%M%S')}"
    run_dir = Path(cfg.artifacts_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run = _Run(run_dir)
    resumed = any(run_dir.glob("*/done.json"))
    manifest = _manifest(run_id, source_sha, cfg, started=started)
    _write_manifest(run, manifest)
    run.log("run_start", run_id=run_id, llm=cfg.llm_enabled, resumed=resumed)
    fallbacks: list[str] = []

    if not pdf_path.is_file():
        _write_manifest(run, _manifest(run_id, "", cfg, status="failed", started=started,
                                       finished=datetime.now(timezone.utc),
                                       limitations=[f"Source PDF not found: {pdf_path}"]))
        raise FileNotFoundError(f"Source PDF not found: {pdf_path}")

    run.json("config.json", cfg.model_dump(mode="json"))
    if gateways is None and cfg.llm_enabled:
        if gateway is not None:
            gateways = {"text": gateway, "vision": None, "image": None}
        else:
            from src.llm import make_gateways
            gateways = make_gateways(cfg)

    # v3.3 F-04: scanned pages get an invisible OCR text layer in a copy of the source; every Stage reads
    # that copy (the Run keeps the ORIGINAL file's sha256 as its identity)
    work_pdf = _ocr_source(pdf_path, run, cfg)
    stages = _build_stages(work_pdf, cfg, run, gateways, source_sha)
    summary: dict[str, Any] = {}
    try:
        summary = run_stages(stages, run_dir, source_sha, cfg, fresh=fresh, until=until,
                             log=run.log, verbose=True)
        for name in summary["executed"]:
            run.state["stages"][name] = "done"
        run.save_state()
        values = summary["values"]
        for name in ("write", "qa"):
            if name in values and name in summary["reused"]:
                _compat_outputs(run, name, values[name])

        report = values.get("infer") or {}
        data_ok = bool((values.get("generate") or {}).get("validation_ok"))
        privacy_ok = bool((values.get("privacy_audit") or {}).get("ok"))
        qa_value = values.get("qa") or {}
        # v3.2 section 9 + v3.3 section 5 gates, each read from the Stage output that proves it
        from src.gates import compute_gates
        gate_report = compute_gates(values, run_dir, cfg, pdf_path)
        gates = {"validation": data_ok, "privacy_audit": privacy_ok, **gate_report["gates"]}
        run.json("gates.json", gate_report)
        pdf_out = qa_value.get("pdf")
        limitations = list((values.get("privacy_audit") or {}).get("limitations") or [])
        limitations.append("Hosted model versions, library versions and installed fonts can change "
                           "the rendered output.")
        if not all(gates.values()):
            limitations.append("Some gates are false; see rendered_pdf_qa.json and the QA summary.")
        status = "ok" if pdf_out and Path(pdf_out).is_file() and all(gates.values()) else "failed"
        outputs = _hash_outputs(run_dir)
        manifest = _manifest(run_id, source_sha, cfg, status=status, started=started,
                             finished=datetime.now(timezone.utc), outputs=outputs, gates=gates,
                             limitations=limitations,
                             llm={"enabled": cfg.llm_enabled, "fallbacks": fallbacks})
        manifest_path = _write_manifest(run, manifest)
        run.json("result.json", {"run_id": run_id, "status": status, "pdf_path": pdf_out,
                                 "gates": gates, "stages": {"executed": summary["executed"],
                                                            "reused": summary["reused"]}})
        run.log("run_end", status=status, gates=gates)
        return RunResult(run_id=run_id, run_dir=str(run_dir), status=status,
                         manifest_path=str(manifest_path), pdf_path=pdf_out)
    except _PartialRun as stopped:
        run.log("run_partial", stage=stopped.stage, message=stopped.message)
        outputs = _hash_outputs(run_dir)
        manifest = _manifest(run_id, source_sha, cfg, status="partial", started=started,
                             finished=datetime.now(timezone.utc), outputs=outputs, gates={},
                             limitations=[stopped.message],
                             llm={"enabled": cfg.llm_enabled, "fallbacks": fallbacks})
        manifest_path = _write_manifest(run, manifest)
        run.json("result.json", {"run_id": run_id, "status": "partial",
                                 "message": stopped.message})
        return RunResult(run_id=run_id, run_dir=str(run_dir), status="partial",
                         manifest_path=str(manifest_path), pdf_path=None)
    except Exception as error:
        run.log("run_error", error=f"{type(error).__name__}: {error}")
        _write_manifest(run, _manifest(run_id, source_sha, cfg, status="failed", started=started,
                                       finished=datetime.now(timezone.utc),
                                       limitations=[f"Run failed: {type(error).__name__}"]))
        raise


# ---------------------------------------------------------------- small helpers
def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _document(run: _Run, source_sha: str):
    blueprint = _read_json(run.dir / "blueprint.json") or {}
    return _source_document(blueprint, source_sha)


def _load_dataset(run: _Run, cfg: Config) -> CanonicalDataset:
    from src.report_stages import load_run_dataset
    data, _report = load_run_dataset(run.dir, cfg)
    return data


def _with_charts(data: CanonicalDataset, plan: dict | None) -> CanonicalDataset:
    """Every chart the Document Plan pairs with a table becomes a registered ChartSpec."""
    data.charts = register_charts(data, plan)
    return data


def _cache_dir(cfg: Config) -> Path:
    path = Path(cfg.extraction.cache_dir)
    return path if path.is_absolute() else REPO_ROOT / path


def _extract(pdf_path: Path, run: _Run, cfg: Config):
    """E-37: parse THIS PDF into the run folder, then run the frozen blueprint and planner code there."""
    summary = extract_structured(pdf_path, run.dir, cache_dir=_cache_dir(cfg),
                                 method=cfg.extraction.method, models_dir=cfg.extraction.models_dir)
    run.log("extraction", method=summary["method"], cached=summary["cached"],
            tables=summary["table_count"], figures=len(summary.get("saved_figures", [])),
            warnings=summary.get("warnings", []))
    blueprint = build_blueprint(pdf_path, project_root=run.dir)
    plan = build_document_plan(blueprint, out_dir=run.dir / "outputs" / "plan")
    run.json("blueprint.json", blueprint)
    run.json("document_plan.json", plan)
    return blueprint, plan


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
