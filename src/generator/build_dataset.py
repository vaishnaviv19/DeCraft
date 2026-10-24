# src/generator/build_dataset.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.inference.schema import InferenceReport


def build_dataset(tables: Dict[str, Any], report: InferenceReport, out_dir: Path = None) -> Dict[str, Any]:
    if out_dir is None:
        out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    # save
    for name, df in tables.items():
        df.to_csv(out_dir / f"synthetic_{name}.csv", index=False)
    (out_dir / "inference.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "lineage.json").write_text(json.dumps(report.lineage or {}, indent=2), encoding="utf-8")
    (out_dir / "schema.json").write_text(json.dumps({"tables": list(tables.keys())}, indent=2), encoding="utf-8")
    return {"tables": list(tables.keys())}
