# src/blueprint/build_blueprint.py
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict

from jsonschema import validate, ValidationError

from .validate_pdf import validate_pdf
from .extract_text_style import extract_text_blocks
from .extract_structure import detect_headings, extract_page_structure
from .extract_tables_schema import extract_table_schemas
from .extract_figures_charts import extract_charts
from .extract_palette import extract_palette
from .extract_entity import extract_entity


def build_blueprint(pdf_path: str | Path, project_root: Path | None = None) -> Dict[str, Any]:
    root = project_root or Path.cwd()
    pdf_path = Path(pdf_path)

    # FR-A1
    validation_info = validate_pdf(pdf_path)

    # FR-A2
    text_blocks = extract_text_blocks(pdf_path)

    # FR-A3, A7
    headings = detect_headings(text_blocks)
    page_structure = extract_page_structure(pdf_path, text_blocks)

    # FR-A4 - prefer reconstructed tables if available
    tables_dir = root / "outputs" / "structured" / "reconstructed_tables"
    if not tables_dir.exists():
        tables_dir = root / "outputs" / "structured" / "tables"
    table_schemas = extract_table_schemas(tables_dir)

    # FR-A5
    figures_dir = root / "outputs" / "structured" / "figures"
    charts = extract_charts(figures_dir)

    # FR-A6
    palette = extract_palette(text_blocks, figures_dir)

    # FR-A8
    entity = extract_entity(text_blocks, headings)

    blueprint = {
        "blueprint_id": str(uuid.uuid4()),
        "source": {
            "pdf_path": validation_info["path"],
            "filename": validation_info["filename"],
            "num_pages": validation_info["num_pages"],
        },
        "validation": validation_info,
        "text_blocks": text_blocks,
        "headings": headings,
        "tables": table_schemas,
        "figures_charts": charts,
        "color_palette": palette,
        "page_structure": page_structure,
        "entity": entity,
    }

    # FR-A9: validate
    schema_path = Path(__file__).parent / "schema" / "blueprint_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        validate(instance=blueprint, schema=schema)
    except ValidationError as e:
        raise RuntimeError(f"Blueprint schema validation failed: {e.message}") from e

    out_dir = root / "outputs" / "blueprint"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "blueprint.json"
    out_path.write_text(json.dumps(blueprint, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved Blueprint JSON: {out_path}")
    return blueprint
