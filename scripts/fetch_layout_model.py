"""Download the DocLayout-YOLO DocStructBench weights once (v3.3 F-05) and print their sha256.

    python scripts/fetch_layout_model.py

The file goes to the path in config `style.layout_model_weights`; record the printed sha256 in
PROMPT_LOG.md. The pipeline itself never downloads anything.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.style._layout_check import WEIGHTS_FILE, WEIGHTS_REPO  # noqa: E402


def main() -> int:
    from huggingface_hub import hf_hub_download
    target = ROOT / load_config().style.layout_model_weights
    target.parent.mkdir(parents=True, exist_ok=True)
    cached = hf_hub_download(repo_id=WEIGHTS_REPO, filename=WEIGHTS_FILE)
    shutil.copyfile(cached, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"weights: {target}\nsha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
