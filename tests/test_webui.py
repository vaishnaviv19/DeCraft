# tests/test_webui.py  -- the studio's upload store and download zip (no pipeline run)
import io
import zipfile

import pytest

from src.webui.server import Studio, _safe_name, decoy_name


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(Studio, "_worker", lambda self: None)       # never start the pipeline
    return Studio(tmp_path)


def test_only_real_pdfs_are_accepted(studio):
    with pytest.raises(ValueError):
        studio.add("b1", "notes.pdf", "", b"hello", llm=False, fresh=False)
    job = studio.add("b1", "report.pdf", "reports/q1", b"%PDF-1.7 test", llm=False, fresh=False)
    assert job.status == "queued" and job.folder == "reports/q1"
    assert job.public(studio.artifacts)["progress"]["label"] == "Waiting in line"


def test_names_are_made_safe():
    assert _safe_name("..\\..\\evil.pdf") == "evil.pdf"
    assert _safe_name("a<b>:c.PDF").endswith(".PDF")
    assert decoy_name("Q1 note.pdf") == "Q1 note_decoy.pdf"


def test_the_batch_zip_keeps_the_folder_structure(studio, tmp_path):
    job = studio.add("b2", "note.pdf", "reports/q1", b"%PDF-1.7 x", llm=False, fresh=False)
    decoy = tmp_path / "decoy.pdf"
    decoy.write_bytes(b"%PDF-1.7 decoy")
    job.status, job.pdf, job.result = "done", str(decoy), {"status": "ok", "gates": {"a": True}}
    names = zipfile.ZipFile(io.BytesIO(studio.batch_zip("b2"))).namelist()
    assert names == ["reports/q1/note_decoy.pdf", "decraft_summary.json"]


def test_jobs_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(Studio, "_worker", lambda self: None)
    first = Studio(tmp_path)
    job = first.add("b3", "a.pdf", "", b"%PDF-1.7", llm=True, fresh=False)
    second = Studio(tmp_path)
    assert second.jobs[job.id].llm and second.jobs[job.id].status == "queued"
