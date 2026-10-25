# tests/pipeline/test_golden.py  (M0.0 Golden Baseline: the existing pipeline must not change)
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden"
VOLATILE = re.compile(r"(time|date|timestamp|created|path|run_id|duration|^blueprint_id$|^plan_id$)", re.I)


def normalise(obj):
    """Replace volatile values so two runs of the existing pipeline compare equal."""
    if isinstance(obj, dict):
        return {k: ("<volatile>" if VOLATILE.search(k) else normalise(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalise(v) for v in obj]
    if isinstance(obj, str):
        return obj.replace("\\", "/")
    return obj


@pytest.mark.e2e
def test_existing_pipeline_output_unchanged():
    subprocess.run([sys.executable, "scripts/run_blueprint_and_plan.py"], cwd=ROOT, check=True)
    for rel, golden in [("outputs/blueprint/blueprint.json", "blueprint.json"),
                        ("outputs/plan/document_plan.json", "document_plan.json")]:
        got = normalise(json.loads((ROOT / rel).read_text(encoding="utf-8")))
        want = normalise(json.loads((GOLDEN / golden).read_text(encoding="utf-8")))
        assert got == want, f"{rel} differs from tests/golden/{golden}"
