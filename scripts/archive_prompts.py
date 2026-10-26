# scripts/archive_prompts.py  -- E-24a prompt archive (M10.1)
"""Save every current prompt version under `docs/prompts/<name>_<version>.txt`.

Rules enforced here:

* One name and version means exactly one text. If the archive already holds a different text for the
  current version, the script fails; bump the version in the `PromptSpec` and add a note to
  `docs/prompt_changes.md`.
* A new version is written next to the old ones, so `PROMPT_LOG.md` can show the failed version and
  the fix.

Usage: `python scripts/archive_prompts.py [--check] [--root .]`.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.prompt_registry import all_prompts  # noqa: E402


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def archive(root: Path | str = ROOT, check: bool = False) -> dict[str, list[str]]:
    """Return {'written': [...], 'unchanged': [...], 'errors': [...]}."""
    root = Path(root)
    directory = root / "docs" / "prompts"
    directory.mkdir(parents=True, exist_ok=True)
    written, unchanged, errors = [], [], []
    for name, prompt in all_prompts().items():
        target = directory / f"{name}_{prompt.version}.txt"
        text = prompt.system.rstrip() + "\n"
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if _digest(existing.strip()) != _digest(text.strip()):
                errors.append(f"{target.name}: same version, different text "
                              f"({_digest(existing)[:8]} != {_digest(text)[:8]})")
            else:
                unchanged.append(target.name)
            continue
        if check:
            errors.append(f"{target.name}: missing (run without --check to write it)")
            continue
        target.write_text(text, encoding="utf-8")
        written.append(target.name)
    return {"written": written, "unchanged": unchanged, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive the current PromptSpec texts")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true", help="report only; do not write")
    args = parser.parse_args()
    result = archive(Path(args.root), check=args.check)
    for label in ("written", "unchanged"):
        for name in result[label]:
            print(f"[archive] {label}: {name}")
    for error in result["errors"]:
        print(f"[archive] ERROR: {error}", file=sys.stderr)
    print(f"[archive] {len(result['written'])} written, {len(result['unchanged'])} unchanged, "
          f"{len(result['errors'])} error(s)")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
