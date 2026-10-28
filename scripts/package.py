# scripts/package.py  -- build submission.zip (M10.3)
"""Build the challenge deliverable ZIP.

Contents (M10.3): the executed `solution.ipynb`, `report_output.pdf`, `PROMPT_LOG.md`,
`requirements.txt`; `src/`, `scripts/`, `config/`, `assets/fonts/`, `tests/`, `docs/`;
`data/input/` and `artifacts/llm_cache/` so the notebook can be re-run with
`DECRAFT_CACHE_ONLY=1`.

Never packaged: `.venv`, `__pycache__`, `.git`, `.pytest_cache`, `.env`, the ZIP itself, and any text
file that contains the value of `GROQ_API_KEY` or `CF_API_TOKEN` (or another configured secret). All
names inside the ZIP are relative (POSIX form).

Run: `python scripts/package.py [--out submission.zip]`.
"""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {".venv", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                "node_modules", "outputs"}
EXCLUDE_NAMES = {".env", "submission.zip", ".coverage", "Thumbs.db", ".DS_Store"}
SECRET_ENV = ("GROQ_API_KEY", "CF_API_TOKEN", "CF_ACCOUNT_ID")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".ipynb", ".yaml", ".yml", ".csv",
                 ".html", ".j2", ".toml", ".cfg", ".ini", ".xml", ".svg"}

REQUIRED_FILES = ("solution.ipynb", "report_output.pdf", "PROMPT_LOG.md", "requirements.txt")
REQUIRED_DIRS = ("src", "scripts", "config", "assets/fonts", "tests", "docs", "data/input",
                 "artifacts/llm_cache")


def _secrets() -> set[str]:
    found = set()
    for name in SECRET_ENV:
        value = os.environ.get(name, "")
        if value and len(value) >= 6:
            found.add(value)
    return found


def _contains_secret(path: Path, secrets: set[str]) -> bool:
    if not secrets or path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(secret in text for secret in secrets)


def _walk(base: Path, root: Path):
    for path in sorted(base.rglob("*")):
        if path.is_dir():
            if path.name in EXCLUDE_DIRS:
                continue
            continue
        if path.name in EXCLUDE_NAMES or path.suffix == ".pyc":
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def build_submission(project_root: Path | str = ROOT, out: Path | str | None = None,
                     run_dir: Path | str | None = None, report_pdf: Path | str | None = None,
                     optional: bool = False, keys: set[str] | None = None) -> Path:
    """Write the ZIP and return its path.

    `run_dir` + `report_pdf` let the Pipeline Package Stage package a run that lives outside the
    project tree. `optional=True` skips missing deliverables (used by tests and early runs).
    """
    root = Path(project_root)
    out = Path(out) if out is not None else root / "submission.zip"
    secrets = _secrets() if keys is None else set(keys)
    missing = [name for name in REQUIRED_FILES if not (root / name).exists()]
    missing += [name for name in REQUIRED_DIRS if not (root / name).exists()]
    if missing and not optional:
        raise FileNotFoundError("missing submission files: " + ", ".join(missing))

    files: list[tuple[Path, str]] = []

    def add(path: Path, arcname: str) -> None:
        if path.is_file() and path.name not in EXCLUDE_NAMES and path.suffix != ".pyc" \
                and not _contains_secret(path, secrets):
            files.append((path, arcname))

    for name in REQUIRED_FILES:
        path = root / name
        if not path.exists():
            continue
        if name == "report_output.pdf" and report_pdf and Path(report_pdf).is_file():
            add(Path(report_pdf), name)
        else:
            add(path, name)
    if report_pdf and Path(report_pdf).is_file() and not any(arc == "report_output.pdf"
                                                            for _, arc in files):
        add(Path(report_pdf), "report_output.pdf")
    for name in REQUIRED_DIRS:
        base = root / name
        if not base.exists():
            continue
        for path in _walk(base, root):
            add(path, path.relative_to(root).as_posix())

    # The run's own generated report, when it is not already the project-root copy.
    if run_dir is not None:
        run = Path(run_dir)
        for name in ("report_output.pdf", "manifest.json"):
            path = run / name
            if path.exists() and not any(arc == name for _, arc in files):
                add(path, f"artifacts/run/{name}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen: set[str] = set()
        for path, arcname in files:
            if arcname in seen:
                continue
            seen.add(arcname)
            archive.write(path, arcname)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DeCraft submission.zip")
    parser.add_argument("--out", default=str(ROOT / "submission.zip"))
    parser.add_argument("--project", default=str(ROOT))
    args = parser.parse_args()
    path = build_submission(Path(args.project), Path(args.out))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    print(f"[package] {path} ({len(names)} entries)")
    bad = [name for name in names if name.startswith(("/", "\\")) or ":" in name]
    print(f"[package] non-relative names: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
