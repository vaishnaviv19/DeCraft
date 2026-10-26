# src/core/recovered.py
"""Turn a Recovered Chart into a hidden Source Table (E-19); shared by style and generation."""
from __future__ import annotations

from src.contracts import SourceTable
from src.contracts.report import RecoveredChart


def recovered_to_source_table(chart: RecoveredChart) -> SourceTable:
    """A hidden source table for a recovered chart: categories first, then one column per series."""
    header = ["Category", *[series.name for series in chart.series]]
    rows = []
    for index, category in enumerate(chart.categories):
        rows.append([str(category), *[f"{series.values[index]:g}" for series in chart.series]])
    return SourceTable(table_id=f"rc_{chart.figure_id}", page=1,
                       caption=f"Recovered from {chart.figure_id}", header=header, raw_cells=rows,
                       extraction_method="vision_recovery")
