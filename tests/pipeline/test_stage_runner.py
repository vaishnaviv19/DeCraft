# tests/pipeline/test_stage_runner.py
from pathlib import Path

import pytest

from src.stage_runner import STAGE_NAMES, Stage, run_stages


def fake_stages(record: list[str]) -> list[Stage]:
    def make(name: str) -> Stage:
        def run(cache):
            record.append(name)
            return {"stage": name, "previous": sorted(cache)}
        return Stage(name, run)
    return [make(name) for name in STAGE_NAMES]


def test_runs_exactly_the_stages_up_to_the_target(tmp_path):
    record: list[str] = []
    summary = run_stages(fake_stages(record), "templates", tmp_path, fresh=True, out=lambda _: None)
    assert record == ["style", "templates"]
    assert summary["executed"] == ["style", "templates"] and summary["reused"] == []


def test_a_second_run_reuses_saved_outputs(tmp_path):
    record: list[str] = []
    stages = fake_stages(record)
    run_stages(stages, "templates", tmp_path, out=lambda _: None)
    record.clear()
    summary = run_stages(stages, "templates", tmp_path, out=lambda _: None)
    assert record == [] and summary["reused"] == ["style", "templates"]
    assert summary["output"]["stage"] == "templates"


def test_fresh_reruns_everything(tmp_path):
    record: list[str] = []
    stages = fake_stages(record)
    run_stages(stages, "style", tmp_path, out=lambda _: None)
    record.clear()
    run_stages(stages, "templates", tmp_path, fresh=True, out=lambda _: None)
    assert record == ["style", "templates"]


def test_the_summary_is_printed_as_json(tmp_path):
    printed: list[str] = []
    run_stages(fake_stages([]), "style", tmp_path, fresh=True, out=printed.append)
    assert printed and '"until": "style"' in printed[0]


def test_an_unknown_stage_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_stages(fake_stages([]), "nope", tmp_path, out=lambda _: None)


def test_the_stage_script_uses_only_a_run_of_the_given_pdf(tmp_path):
    """E-37: --pdf X never picks up another PDF's run, even a newer one."""
    import importlib.util
    import json as _json
    import os
    from pathlib import Path as _Path

    import pytest as _pytest

    from src.config import load_config
    from src.extraction import sha256_file
    spec = importlib.util.spec_from_file_location("stage_cli", _Path(__file__).resolve().parents[2] / "scripts" / "stage.py")
    stage_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage_cli)
    mine, other = tmp_path / "mine.pdf", tmp_path / "other.pdf"
    mine.write_bytes(b"%PDF mine"); other.write_bytes(b"%PDF other")
    runs = tmp_path / "artifacts" / "runs"
    for name, pdf, age in (("run_mine", mine, 100), ("run_other", other, 0)):     # the other run is newer
        d = runs / name
        (d / "canonical_dataset").mkdir(parents=True)
        (d / "inference.json").write_text("{}")
        (d / "manifest.json").write_text(_json.dumps({"source_sha256": sha256_file(pdf)}))
        os.utime(d / "manifest.json", (1_000_000 - age, 1_000_000 - age))
    cfg = load_config(artifacts_dir=str(tmp_path / "artifacts"))
    assert stage_cli.latest_run(cfg, mine).name == "run_mine"
    with _pytest.raises(FileNotFoundError):
        stage_cli.latest_run(cfg, tmp_path / "third.pdf") if (tmp_path / "third.pdf").write_bytes(b"x") else None
