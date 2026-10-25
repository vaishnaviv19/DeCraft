# tests/blueprint/test_to_source_document.py
import hashlib
import json

import pytest

from src.blueprint import to_source_document

CSV = 'Company,Revenue,Growth\n"Acme, Ltd","$1,250.40",\nBeta,"$2,000.00",12.5%\n'


def blueprint_for(tmp_path, csv_text=CSV, page=3):
    csv_path = tmp_path / "table_001.csv"
    csv_path.write_text(csv_text, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps(
        [{"table_number": 1, "page": page, "rows": 2, "columns": 3}]), encoding="utf-8")
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf, only bytes for hashing")
    return {
        "blueprint_id": "bp-1",
        "source": {"pdf_path": str(pdf), "filename": "source.pdf", "num_pages": 5},
        "validation": {"title": None},
        "entity": {"entity_name": "Acme", "domain_industry": "general", "tone": "formal"},
        "text_blocks": [
            {"page": 3, "text": "Revenue grew in the quarter."},
            {"page": 3, "text": "All figures in Rs. cr."},
            {"page": 4, "text": "Other page text."},
        ],
        "tables": [{
            "table_id": "table_001",
            "source_csv": str(csv_path),
            "column_headers": ["Company", "Revenue", "Growth"],
            "column_count": 3,
            "row_count": 2,
            "columns": [],
        }],
    }


def test_raw_cells_keep_the_original_strings(tmp_path):
    doc = to_source_document(blueprint_for(tmp_path))
    t = doc.tables[0]
    assert t.header == ["Company", "Revenue", "Growth"]
    assert t.raw_cells == [["Acme, Ltd", "$1,250.40", ""], ["Beta", "$2,000.00", "12.5%"]]


def test_page_comes_from_the_reconstructed_table_manifest(tmp_path):
    assert to_source_document(blueprint_for(tmp_path)).tables[0].page == 3


def test_page_falls_back_to_one_without_a_manifest(tmp_path):
    bp = blueprint_for(tmp_path)
    (tmp_path / "manifest.json").unlink()
    assert to_source_document(bp).tables[0].page == 1


def test_entity_title_and_context_text(tmp_path):
    doc = to_source_document(blueprint_for(tmp_path))
    assert doc.entity_name == "Acme"
    assert doc.title == "source"
    assert "Revenue grew" in doc.tables[0].context_text
    assert "Other page text" not in doc.tables[0].context_text
    assert doc.text_blocks[0] == "Revenue grew in the quarter."


def test_document_hash_is_the_source_pdf_hash(tmp_path):
    bp = blueprint_for(tmp_path)
    expected = hashlib.sha256(open(bp["source"]["pdf_path"], "rb").read()).hexdigest()
    assert to_source_document(bp).document_sha256 == expected


def test_missing_source_csv_becomes_an_empty_bad_table(tmp_path):
    bp = blueprint_for(tmp_path)
    bp["tables"][0]["source_csv"] = str(tmp_path / "absent.csv")
    t = to_source_document(bp).tables[0]
    assert t.header == ["Company", "Revenue", "Growth"] and t.raw_cells == []


def test_blueprint_is_not_mutated(tmp_path):
    bp = blueprint_for(tmp_path)
    before = json.dumps(bp, sort_keys=True)
    to_source_document(bp)
    assert json.dumps(bp, sort_keys=True) == before
