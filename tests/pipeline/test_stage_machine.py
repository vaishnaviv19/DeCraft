# tests/pipeline/test_stage_machine.py
"""M9.1: the Stage machine persists done.json, skips matching input hashes, and stops cleanly."""
import json
from pathlib import Path

import pytest

from src.config import load_config
from src.errors import CacheMiss, LLMBudgetExceeded
from src.pipeline import PipelineStage, _PartialRun, run, run_stages

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "input" / "sample_input_1.pdf"


def config(tmp_path, **overrides):
    return load_config(artifacts_dir=str(tmp_path / "artifacts"), **overrides)


def _stages(calls: list[str], fail: dict[str, Exception] | None = None,
            constant: dict[str, object] | None = None):
    fail = fail or {}
    constant = constant or {}

    def make(name: str):
        def go(cache):
            calls.append(name)
            if name in fail:
                raise fail[name]
            return constant.get(name, {"name": name, "seen": len(cache)})
        return go

    return [PipelineStage("extract", make("extract"), ("seed",)),
            PipelineStage("infer", make("infer"), ("report",)),
            PipelineStage("write", make("write"), ("report",))]


def test_a_rerun_skips_every_stage_whose_inputs_match(tmp_path):
    cfg = config(tmp_path)
    calls: list[str] = []
    root = tmp_path / "run"
    first = run_stages(_stages(calls), root, "sha", cfg)
    assert first["executed"] == ["extract", "infer", "write"]
    assert (root / "extract" / "done.json").exists()
    done = json.loads((root / "extract" / "done.json").read_text(encoding="utf-8"))
    assert set(done) == {"stage", "input_hash", "output_hash"}

    second = run_stages(_stages(calls), root, "sha", cfg)
    assert second["executed"] == [] and second["reused"] == ["extract", "infer", "write"]
    assert calls == ["extract", "infer", "write"]


def test_changing_a_config_part_reruns_from_the_first_affected_stage(tmp_path):
    cfg = config(tmp_path)
    calls: list[str] = []
    root = tmp_path / "run"
    run_stages(_stages(calls, constant={"extract": {"fixed": True}}), root, "sha", cfg)
    calls.clear()

    changed = load_config(artifacts_dir=str(tmp_path / "artifacts"),
                          report={**cfg.report.model_dump(), "min_total_words": 1200})
    result = run_stages(_stages(calls, constant={"extract": {"fixed": True}}), root, "sha", changed)
    assert result["reused"] == ["extract"]                 # does not read `report`
    assert result["executed"] == ["infer", "write"]         # first affected Stage and later ones


def test_a_fresh_run_ignores_done_files(tmp_path):
    cfg = config(tmp_path)
    calls: list[str] = []
    root = tmp_path / "run"
    run_stages(_stages(calls), root, "sha", cfg)
    calls.clear()
    result = run_stages(_stages(calls), root, "sha", cfg, fresh=True)
    assert result["executed"] == ["extract", "infer", "write"]
    assert calls == ["extract", "infer", "write"]


@pytest.mark.parametrize("error", [LLMBudgetExceeded("quota spent"), CacheMiss("no cache")])
def test_a_quota_or_cache_stop_leaves_a_resumable_stage(tmp_path, error):
    cfg = config(tmp_path)
    root = tmp_path / "run"
    calls: list[str] = []
    stages = _stages(calls, fail={"infer": error})
    with pytest.raises(_PartialRun) as raised:
        run_stages(stages, root, "sha", cfg)
    assert raised.value.stage == "infer"
    assert (root / "extract" / "done.json").exists()
    assert not (root / "infer" / "done.json").exists()

    calls.clear()
    result = run_stages(_stages(calls), root, "sha", cfg)
    assert result["reused"] == ["extract"]
    assert result["executed"] == ["infer", "write"]


def test_run_writes_a_partial_manifest_when_a_stage_spends_the_budget(tmp_path, monkeypatch):
    import src.pipeline as pipeline

    def fake_stages(pdf, cfg, run, gateways, source_sha=None):
        return [PipelineStage("extract", lambda cache: {"ok": True}, ("seed",)),
                PipelineStage("infer", lambda cache: (_ for _ in ()).throw(
                    LLMBudgetExceeded("stage budget exhausted")), ("seed",)),
                PipelineStage("write", lambda cache: {"ok": True}, ("seed",))]

    monkeypatch.setattr(pipeline, "_build_stages", fake_stages)
    result = run(SAMPLE, config(tmp_path))
    assert result.status == "partial"
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert any("infer" in item and "LLMBudgetExceeded" in item for item in manifest["limitations"])
    assert (Path(result.run_dir) / "extract" / "done.json").exists()
    assert not (Path(result.run_dir) / "write" / "done.json").exists()
