# tests/extraction/test_extraction.py  (E-37: every run parses the PDF it is given)
import json
from pathlib import Path

import pandas as pd
import pymupdf
import pytest

from src.config import load_config
from src.extraction import extract_structured, sha256_file, structured_dir

ROWS = [["Route", "Trips", "Revenue"], ["North loop", "120", "4,500"], ["Harbour run", "95", "3,100"],
        ["Airport link", "60", "2,250"]]


def make_pdf(path: Path, rows=ROWS, title="Bluefin Logistics Quarterly Update") -> Path:
    """A one-page PDF with a ruled table, like a real report table."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), title, fontsize=16)
    page.insert_text((72, 90), "Bluefin Logistics moved more freight this quarter than last.", fontsize=10)
    x0, y0, cw, rh = 72, 120, 140, 22
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), value, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def read_tables(root: Path) -> list[pd.DataFrame]:
    folder = structured_dir(root) / "reconstructed_tables"
    return [pd.read_csv(p, dtype=str, keep_default_na=False) for p in sorted(folder.glob("table_*.csv"))]


def test_pymupdf_extracts_the_tables_of_the_given_pdf(tmp_path):
    pdf = make_pdf(tmp_path / "bluefin.pdf")
    summary = extract_structured(pdf, tmp_path / "run", method="pymupdf")
    tables = read_tables(tmp_path / "run")
    assert summary["method"] == "pymupdf" and summary["table_count"] == 1 and len(tables) == 1
    assert list(tables[0].columns) == ROWS[0]
    assert tables[0]["Route"].tolist() == ["North loop", "Harbour run", "Airport link"]
    manifest = json.loads((structured_dir(tmp_path / "run") / "reconstructed_tables" / "manifest.json").read_text())
    assert manifest[0]["page"] == 1
    assert summary["source_sha256"] == sha256_file(pdf)


def test_the_cache_is_reused_only_for_the_identical_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    first = make_pdf(tmp_path / "a.pdf")
    assert extract_structured(first, tmp_path / "r1", cache, method="pymupdf")["cached"] is False
    assert extract_structured(first, tmp_path / "r2", cache, method="pymupdf")["cached"] is True
    other = make_pdf(tmp_path / "b.pdf", rows=[["Gene", "Reads"], ["abc1", "10"], ["xyz9", "12"]], title="Other")
    summary = extract_structured(other, tmp_path / "r3", cache, method="pymupdf")
    assert summary["cached"] is False
    assert list(read_tables(tmp_path / "r3")[0].columns) == ["Gene", "Reads"]


def test_a_stale_run_folder_is_replaced_not_mixed(tmp_path):
    root = tmp_path / "run"
    stale = structured_dir(root) / "reconstructed_tables"
    stale.mkdir(parents=True)
    (stale / "table_099.csv").write_text("Old,Table\n1,2\n", encoding="utf-8")
    extract_structured(make_pdf(tmp_path / "x.pdf"), root, method="pymupdf")
    assert not (stale / "table_099.csv").exists()


def test_auto_falls_back_to_pymupdf_when_docling_fails(tmp_path, monkeypatch):
    from src.extraction import _docling

    def broken(*_a, **_k):
        raise RuntimeError("models missing")
    monkeypatch.setattr(_docling, "extract", broken)
    summary = extract_structured(make_pdf(tmp_path / "x.pdf"), tmp_path / "run", method="auto")
    assert summary["method"] == "pymupdf"
    assert any("docling failed" in w for w in summary["warnings"])


def test_docling_only_raises_instead_of_switching(tmp_path, monkeypatch):
    from src.extraction import _docling
    monkeypatch.setattr(_docling, "extract", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        extract_structured(make_pdf(tmp_path / "x.pdf"), tmp_path / "run", method="docling")


def test_pipeline_blueprint_comes_from_the_run_pdf_not_the_sample(tmp_path, monkeypatch):
    """The old bug: any PDF got the sample's 12 tables from the repo's outputs/structured."""
    from src import pipeline
    repo_blueprint = pipeline.REPO_ROOT / "outputs" / "blueprint" / "blueprint.json"
    before = repo_blueprint.stat().st_mtime if repo_blueprint.exists() else None
    pdf = make_pdf(tmp_path / "bluefin.pdf")
    cfg = load_config(artifacts_dir=str(tmp_path / "artifacts"), llm_enabled=False,
                      extraction={"method": "pymupdf", "cache_dir": str(tmp_path / "cache")})
    run = pipeline._Run(tmp_path / "rundir")
    blueprint, plan = pipeline._extract(pdf, run, cfg)
    assert len(blueprint["tables"]) == 1
    assert blueprint["tables"][0]["column_headers"] == ROWS[0]
    assert str(tmp_path / "rundir") in blueprint["tables"][0]["source_csv"]
    assert (tmp_path / "rundir" / "outputs" / "plan" / "document_plan.json").exists()
    after = repo_blueprint.stat().st_mtime if repo_blueprint.exists() else None
    assert before == after, "a run must not overwrite the repo's outputs/blueprint"
