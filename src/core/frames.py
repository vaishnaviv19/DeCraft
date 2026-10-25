# src/core/frames.py
"""The normalised table view used by inference, privacy and generation (Shared Kernel).

Numbers are in Base Units, dates are timestamps, everything else stays text, and `None` marks a
missing value. Raw Cells are never changed; this frame exists only so rules can be tested on rows.
"""
from __future__ import annotations

import pandas as pd

from src.contracts import FieldProfile, SourceTable
from src.core.dates import to_calendar_dates
from src.core.missingness import token_kind
from src.core.values import parse_number


def table_frame(table: SourceTable, profiles: list[FieldProfile]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Returns the frame and the Decimals map the formula tolerance needs."""
    cols: dict[str, pd.Series] = {}
    decimals: dict[str, int] = {}
    for j, profile in enumerate(profiles):
        raw = [row[j] if j < len(row) else None for row in table.raw_cells]
        if profile.physical_type in {"integer", "float"}:
            cols[profile.column] = pd.Series(
                [v * profile.scale if (v := parse_number(x)) is not None else None for x in raw],
                dtype="float64")
            if profile.decimals is not None:
                decimals[profile.column] = profile.decimals
        elif profile.physical_type == "date":
            # Day/month order is never guessed: ambiguous 03/04/2025 needs the column's own evidence
            # (see core.dates); unreadable dates become missing here, never a silent wrong date.
            cols[profile.column] = to_calendar_dates(
                pd.Series([None if token_kind(x) else str(x) for x in raw], dtype=object))[0]
        else:
            cols[profile.column] = pd.Series(
                [None if token_kind(x) else str(x).strip() for x in raw], dtype=object)
    return pd.DataFrame(cols, index=range(len(table.raw_cells))), decimals
