# tests/pipeline/test_reproducibility.py
"""M10.2: two runs with the same seed and Config produce identical dataset hashes."""
import json
import shutil
from pathlib import Path

from src.config import load_config
from src.pipeline import run

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "input" / "sample_input_1.pdf"


def dataset_hashes(result) -> dict[str, str]:
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    return {name: digest for name, digest in manifest["outputs"].items()
            if name.startswith("canonical_dataset/")}


def test_two_runs_with_the_same_seed_have_identical_dataset_hashes(tmp_path):
    cfg = load_config(artifacts_dir=str(tmp_path / "artifacts"), llm_enabled=False)
    first = run(SAMPLE, cfg)
    hashes_first = dataset_hashes(first)
    shutil.rmtree(tmp_path / "artifacts")          # force a cold second run, not a resume
    second = run(SAMPLE, cfg)
    hashes_second = dataset_hashes(second)
    assert hashes_first and hashes_first == hashes_second
    assert first.run_id == second.run_id
