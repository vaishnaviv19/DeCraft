# src/stage_runner.py  -- run report-generation stages up to a named stage, reusing saved outputs
"""M8.0.4: a small stage machine for development.

Every stage reads the outputs of earlier stages, writes `output.json` and `done.json` (a hash of its
inputs) under `<root>/<stage>/` (CLI `--dir`, default `artifacts/runs/dev`), and is skipped when that hash still
matches. `--fresh` ignores the saved state. The CLI is `scripts/stage.py`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

STAGE_NAMES = ("style", "templates", "figures", "prepare", "charts", "plan", "write", "fit",
               "images", "render", "qa")


@dataclass(frozen=True)
class Stage:
    name: str
    run: Callable[[dict[str, Any]], Any]


def _hash_inputs(cache: dict[str, Any]) -> str:
    payload = json.dumps(cache, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, default=str)


def run_stages(stages: list[Stage], until: str, root: Path, fresh: bool = False,
               out: Callable[[str], None] = print, salt: str = "") -> dict[str, Any]:
    """Run stages in order up to `until`; return the JSON summary that is also printed."""
    names = [stage.name for stage in stages]
    if until not in names:
        raise ValueError(f"unknown stage {until!r}; expected one of {names}")
    selected = stages[: names.index(until) + 1]
    root = Path(root)
    cache: dict[str, Any] = {}
    executed: list[str] = []
    reused: list[str] = []

    for stage in selected:
        directory = root / stage.name
        output_path = directory / "output.json"
        done_path = directory / "done.json"
        input_hash = _hash_inputs({"cache": cache, "mode": salt})
        if not fresh and done_path.exists() and output_path.exists():
            try:
                if json.loads(done_path.read_text(encoding="utf-8")).get("input_hash") == input_hash:
                    cache[stage.name] = json.loads(output_path.read_text(encoding="utf-8"))
                    reused.append(stage.name)
                    continue
            except json.JSONDecodeError:
                pass                      # a damaged record means: run the stage again
        value = stage.run(dict(cache))
        directory.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_dump(value), encoding="utf-8")
        done_path.write_text(json.dumps({"stage": stage.name, "input_hash": input_hash}),
                             encoding="utf-8")
        cache[stage.name] = value
        executed.append(stage.name)

    summary = {"until": until, "executed": executed, "reused": reused, "dir": str(root),
               "output": cache.get(until)}
    out(json.dumps(summary, indent=2, default=str))
    return summary
