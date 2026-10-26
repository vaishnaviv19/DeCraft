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
