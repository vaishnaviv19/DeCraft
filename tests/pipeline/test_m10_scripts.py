# tests/pipeline/test_m10_scripts.py
"""M10: prompt archive/log builder and the submission builder."""
import json
import zipfile
from pathlib import Path

from scripts.archive_prompts import archive
from scripts.build_prompt_log import build_prompt_log
from scripts.package import build_submission
from src.llm import PromptSpec


def test_archive_writes_a_new_version_and_rejects_a_silent_text_change(tmp_path):
    prompts = {"widget": PromptSpec(name="widget", version="v2", system="Widget v2 text.")}
    notes = tmp_path / "docs" / "prompts"
    notes.mkdir(parents=True)
    (notes / "widget_v1.txt").write_text("Widget v1 text.\n", encoding="utf-8")

    # The helper uses the real registry, so exercise the real prompt set and then a one-off copy.
    first = archive(tmp_path)
    assert not first["errors"]
    assert (notes / "annotator_v5.txt").exists()

    target = notes / "annotator_v5.txt"
    target.write_text("different text\n", encoding="utf-8")
    second = archive(tmp_path)
    assert any("annotator_v5.txt" in error and "same version" in error for error in second["errors"])


def test_prompt_log_contains_every_name_version_and_failure_count(tmp_path):
    prompts = {
        "alpha": PromptSpec(name="alpha", version="v2", system="Alpha v2.", temperature=0.1,
                            rationale="Because alpha.", max_input_tokens=100, max_output_tokens=20),
        "beta": PromptSpec(name="beta", version="v3", system="Beta v3.", role="vision",
                           rationale="Because beta.", max_input_tokens=200, max_output_tokens=30),
    }
    log = tmp_path / "prompt_log.jsonl"
    log.write_text("\n".join(json.dumps(record) for record in [
        {"prompt": "alpha", "version": "v1", "ok": False, "cache_hit": False},
        {"prompt": "alpha", "version": "v2", "ok": True, "cache_hit": False},
        {"prompt": "alpha", "version": "v2", "ok": False, "cache_hit": True},
        {"prompt": "beta", "version": "v3", "ok": True, "cache_hit": True},
    ]) + "\n", encoding="utf-8")
    notes = tmp_path / "docs" / "prompt_changes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("## alpha\n\n| Version | Change |\n| --- | --- |\n| v1 | first try |\n",
                     encoding="utf-8")
    archive_dir = tmp_path / "docs" / "prompts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "alpha_v1.txt").write_text("Alpha v1.", encoding="utf-8")
    (archive_dir / "alpha_v2.txt").write_text("Alpha v2.", encoding="utf-8")
    (archive_dir / "beta_v3.txt").write_text("Beta v3.", encoding="utf-8")
    out = tmp_path / "PROMPT_LOG.md"
    build_prompt_log(tmp_path, log_path=log, notes_path=notes, out_path=out, prompts=prompts,
                     cache_dir=tmp_path / "cache")
    text = out.read_text(encoding="utf-8")
    assert "`alpha`" in text and "`beta`" in text
    assert "| v1 | 1 | 1 | 0 |" in text
    assert "| v2 | 2 | 1 | 1 |" in text
    assert "Alpha v2." in text and "Because alpha." in text
    assert "first try" in text


def test_package_excludes_secrets_and_keeps_every_zip_name_relative(tmp_path):
    root = tmp_path / "project"
    for name in ("solution.ipynb", "report_output.pdf", "PROMPT_LOG.md", "requirements.txt"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("{}", encoding="utf-8")
    for name in ("src", "scripts", "config", "assets/fonts", "tests", "docs", "data/input",
                 "artifacts/llm_cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "keep.txt").write_text("keep", encoding="utf-8")
    (root / ".env").write_text("GROQ_API_KEY=supersecret\n", encoding="utf-8")
    (root / "docs" / "leak.md").write_text("key supersecret here", encoding="utf-8")
    (root / "docs" / "clean.md").write_text("clean", encoding="utf-8")

    out = root / "submission.zip"
    path = build_submission(root, out, keys={"supersecret"})
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert ".env" not in names
    assert "docs/leak.md" not in names
    assert "docs/clean.md" in names
    assert "solution.ipynb" in names and "artifacts/llm_cache/keep.txt" in names
    assert all(not name.startswith(("/", "\\")) and ":" not in name for name in names)


def test_package_can_use_the_run_report_instead_of_a_stale_root_copy(tmp_path):
    root = tmp_path / "project"
    for name in ("solution.ipynb", "report_output.pdf", "PROMPT_LOG.md", "requirements.txt"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("root", encoding="utf-8")
    for name in ("src", "scripts", "config", "assets/fonts", "tests", "docs", "data/input",
                 "artifacts/llm_cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    run = tmp_path / "run"
    run.mkdir()
    report = run / "report_output.pdf"
    report.write_bytes(b"%PDF-new")
    path = build_submission(root, root / "submission.zip", run_dir=run, report_pdf=report,
                            keys=set())
    with zipfile.ZipFile(path) as archive:
        assert archive.read("report_output.pdf") == b"%PDF-new"
