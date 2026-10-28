# src/generation/_grids.py  -- E-51: write the consistent number grids into the generated tables
"""Tables with a row-label column keep their rows; every number cell in them comes from the grid
engine (`src.core.grid`): base cells scaled, totals / margins / growth / repeated figures recomputed.
Numbers kept as text beside labels (a stacked fact sheet) are written in their source format."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.grid import format_cell, number_style, relations_for


def grid_label_nodes(doc, profiles: dict[str, list]) -> set[str]:
    """'table.column' of every grid's row-label column: those rows keep their order (a statement's
    '% change' repeats, so 'unique labels' alone never kept it - live run 2026-10-27: line items
    were shuffled across rows)."""
    grids, _ = relations_for(doc)
    out = set()
    for table_id, grid in grids.items():
        table_profiles = profiles.get(table_id) or []
        if grid.label_col < len(table_profiles):
            out.add(f"{table_id}.{table_profiles[grid.label_col].column}")
    return out


def apply_grids(tables: dict[str, pd.DataFrame], doc, profiles: dict[str, list], rng: np.random.Generator,
                distance: float, relabel=None) -> dict[str, int]:
    """Overwrite every grid number cell of `tables` (base units for number columns, source-formatted
    text for text columns). -> {table_id: cells written}."""
    grids, rel = relations_for(doc)
    values = _generate(rel, rng, distance)
    written: dict[str, int] = {}
    for table_id, grid in grids.items():
        frame, table_profiles = tables.get(table_id), profiles.get(table_id) or []
        if frame is None or len(frame) != len(grid.raw) or len(table_profiles) < len(grid.header):
            continue
        style = number_style(grid)
        count = 0
        for c in grid.ordinals:                     # row numbers stay as printed (1, 2, 3 ...)
            if c < len(table_profiles) and table_profiles[c].column in frame.columns:
                column = table_profiles[c].column
                source = [row[c] if c < len(row) else None for row in grid.raw]
                if table_profiles[c].physical_type in {"integer", "float"}:
                    from src.core.values import parse_number
                    frame[column] = [parse_number(v) * (table_profiles[c].scale or 1.0)
                                     if v is not None and parse_number(v) is not None else float("nan")
                                     for v in source]
                else:
                    frame[column] = pd.Series(source, index=frame.index, dtype=object)
        # text cells beside the numbers stay in their row (a method per unit, a note such as '10% holding
        # discount to investments'); re-drawing them per column turned that note into '91.7%'
        for r, row in enumerate(grid.raw):
            for c, source in enumerate(row):
                if c == grid.label_col or (r, c) in grid.cells or c >= len(table_profiles) or c in grid.ordinals:
                    continue
                profile = table_profiles[c]
                if profile.physical_type != "text" or profile.column not in frame.columns:
                    continue
                if frame[profile.column].dtype != object:
                    frame[profile.column] = frame[profile.column].astype(object)
                # every cell keeps ITS row: a blank stays blank and a sub-header ('FY-24E') stays a
                # sub-header; the column-wise resampling had put another row's '2,07,172' there
                if not str(source).strip():
                    frame.iat[r, frame.columns.get_loc(profile.column)] = None
                    continue
                if r in grid.subheaders:
                    frame.iat[r, frame.columns.get_loc(profile.column)] = str(source).strip()
                    continue
                text = str(source).strip()
                if relabel is not None and relabel.wants(table_id, profile.column):
                    text = relabel.get(table_id, profile.column, text)
                frame.iat[r, frame.columns.get_loc(profile.column)] = _perturb(text, rng, distance)
        for (r, c), _ in grid.cells.items():
            if c >= len(table_profiles):
                continue
            profile = table_profiles[c]
            if profile.column not in frame.columns:
                continue
            value = values[(table_id, r, c)]
            position = frame.columns.get_loc(profile.column)
            if profile.physical_type in {"integer", "float"}:
                if not pd.api.types.is_float_dtype(frame[profile.column]):
                    frame[profile.column] = frame[profile.column].astype("float64")
                frame.iat[r, position] = value * (profile.scale or 1.0)
            else:
                if frame[profile.column].dtype != object:
                    frame[profile.column] = frame[profile.column].astype(object)
                frame.iat[r, position] = format_cell(grid.raw[r][c], value, style)
            count += 1
        written[table_id] = count
    return written


def _perturb(text: str, rng, distance: float) -> str:
    """Numbers inside a text cell move like every other number; the wording stays."""
    from src.generation._tiny import perturb_text_numbers
    return perturb_text_numbers([text], rng, 0.6, 1.6, distance)[0]


def _generate(rel, rng, distance):
    from src.core.grid import generate_numbers
    return generate_numbers(rel, rng, distance)


def restore_ordinals(tables: dict, doc, profiles: dict, key_columns: dict) -> list[str]:
    """A column numbering the rows 1, 2, 3 ... ('Sr. No.') is printed as in the source: fresh keys turned
    it into 58 ... 67 (second sample, 2026-10-27). It stays a key (identifiers are exempt from the value
    distance). -> 'table.column' fixed."""
    from src.core.values import parse_number
    fixed = []
    for table in doc.tables:
        frame, table_profiles = tables.get(table.table_id), profiles.get(table.table_id) or []
        if frame is None or len(frame) != len(table.raw_cells):
            continue
        for j, profile in enumerate(table_profiles):
            raw = [row[j] if j < len(row) else "" for row in table.raw_cells]
            numbers = [parse_number(v) for v in raw if str(v).strip()]
            if len(numbers) < 3 or any(n is None for n in numbers) \
                    or numbers != [float(i) for i in range(int(numbers[0]), int(numbers[0]) + len(numbers))] \
                    or numbers[0] not in (0.0, 1.0) or profile.column not in frame.columns:
                continue
            if profile.physical_type in {"integer", "float"}:
                frame[profile.column] = [parse_number(v) * (profile.scale or 1.0) if str(v).strip() else float("nan")
                                         for v in raw]
            else:
                frame[profile.column] = pd.Series([v if str(v).strip() else None for v in raw],
                                                  index=frame.index, dtype=object)
            # it stays among the key columns: identifiers are exempt from the value distance (row numbers
            # equal to the source are not data) and 1..n is a valid, unique key
            if profile.column not in key_columns.setdefault(table.table_id, []):
                key_columns[table.table_id].append(profile.column)
            fixed.append(f"{table.table_id}.{profile.column}")
    return fixed
