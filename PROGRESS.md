# DeCraft Progress Tracker

Last updated: 2026-10-24

## Goal
Implement FR-A1 through FR-A9 (Blueprint extraction) and FR-B1 through FR-B4 (Document Plan) as specified.

## Current Status
- [x] FR-A1: PDF validation (exists, valid PDF, not encrypted, >=1 page, clear errors)
- [x] FR-A2: Text blocks with font name, size, weight, colour, bounding box per page
- [x] FR-A3: Heading hierarchy (H1/H2/H3) from font-size and weight clustering (no keyword lists)
- [x] FR-A4: Tables + schema (column headers, counts, dtypes, currency, %, units)
- [x] FR-A5: Charts/images with location, bbox, classified chart type (bar/line/pie/other)
- [x] FR-A6: Color palette (dominant + accent) from vector fills, text colours, raster samples
- [x] FR-A7: Page structure (page size, margins, header/footer + pos, page-number format, column layout)
- [x] FR-A8: Entity name, domain/industry, tone
- [x] FR-A9: Blueprint JSON validated against schema
- [x] FR-B1: Document Plan with ordered sections, purpose, target word count, required components
- [x] FR-B2: Sections thematically appropriate, no verbatim copy
- [x] FR-B3: >= 1000 words total, safety margin >= 1500
- [x] FR-B4: Every planned table must declare paired chart spec

## Files Created/Modified
- [x] src/blueprint/__init__.py
- [x] src/blueprint/validate_pdf.py (FR-A1)
- [x] src/blueprint/extract_text_style.py (FR-A2)
- [x] src/blueprint/extract_structure.py (FR-A3, A7)
- [x] src/blueprint/extract_tables_schema.py (FR-A4)
- [x] src/blueprint/extract_figures_charts.py (FR-A5)
- [x] src/blueprint/extract_palette.py (FR-A6)
- [x] src/blueprint/extract_entity.py (FR-A8)
- [x] src/blueprint/schema/blueprint_schema.json (FR-A9)
- [x] src/blueprint/build_blueprint.py (FR-A9 orchestrator)
- [x] src/planner/__init__.py
- [x] src/planner/plan_rules.py (FR-B rules)
- [x] src/planner/build_plan.py (FR-B1-B4)
- [x] src/planner/validate_plan.py (FR-B validation)
- [x] scripts/run_blueprint_and_plan.py (entry point)
- [x] requirements.txt (updated deps)
- [x] PROGRESS.md (created)

## Notes
- Building on top of existing Docling extraction (outputs/structured/*). Using PyMuPDF/pypdf for precise text/style/bbox and validation.
- Clustering by font-size/weight only for headings. No keyword lists.
- Every table in plan gets paired chart spec.
- Plan targets >=1500 words.
