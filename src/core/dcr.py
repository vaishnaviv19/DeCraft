# src/core/dcr.py   (v3.3 fix F-02, Shared Kernel: used by privacy audit AND validation)
"""Distance to Closest Record (DCR) on normalised rows. Numeric columns are scaled by the SOURCE column
range, and share columns (row / column total) are included so proportions are compared, not just raw
values. Categorical columns add 1 per mismatch. Distance = mean over columns (Gower-style, 0..1); a
missing cell counts as fully different. A synthetic row with DCR < dcr_min is a near-duplicate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.contracts import Severity, Violation


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _prep(src: pd.DataFrame, syn: pd.DataFrame, num: list[str], with_shares: bool):
    s_cols = {c: _numeric(src, c) for c in num}
    g_cols = {c: _numeric(syn, c) for c in num}
    if with_shares:
        for c in num:
            ts, tg = s_cols[c].abs().sum(), g_cols[c].abs().sum()
            if ts and tg:
                s_cols[c + "__share"] = s_cols[c].abs() / ts
                g_cols[c + "__share"] = g_cols[c].abs() / tg
    ranges = {c: (float(v.max() - v.min()) or (abs(float(v.max())) if v.notna().any() else 0.0) or 1.0)
              for c, v in s_cols.items()}
    return s_cols, g_cols, ranges


def dcr(src: pd.DataFrame, syn: pd.DataFrame, num: list[str], cat: list[str],
        with_shares: bool = True, nearest: list | None = None) -> np.ndarray:
    """DCR per generated row; `nearest`, when given, receives the index of each row's closest source row."""
    s_cols, g_cols, ranges = _prep(src, syn, num, with_shares)
    k = len(s_cols) + len(cat)
    if k == 0 or len(src) == 0:
        if nearest is not None:
            nearest.extend([0] * len(syn))
        return np.ones(len(syn))
    out = np.empty(len(syn))
    for i in range(len(syn)):
        d = np.zeros(len(src))
        for c, s in s_cols.items():
            diff = np.abs(s.to_numpy(float) - float(g_cols[c].iloc[i])) / ranges[c]
            d += np.nan_to_num(np.minimum(diff, 1.0), nan=1.0)
        for c in cat:
            d += (src[c].astype(str).to_numpy() != str(syn[c].iloc[i])).astype(float)
        out[i] = float(np.min(d) / k)
        if nearest is not None:
            nearest.append(int(np.argmin(d)))
    return out


def _far_in_every_cell(src: pd.DataFrame, syn: pd.DataFrame, num: list[str], i: int, j: int,
                       min_cell: float) -> bool:
    """Generated row i differs >= min_cell (relative) from source row j in every numeric cell."""
    if j >= len(src):
        return False
    for c in num:
        a, b = pd.to_numeric(pd.Series([src[c].iloc[j]]), errors="coerce").iloc[0],             pd.to_numeric(pd.Series([syn[c].iloc[i]]), errors="coerce").iloc[0]
        if pd.isna(a) or pd.isna(b) or a == 0:
            continue
        if abs(b - a) / abs(a) < min_cell:
            return False
    return True


def dcr_violations(table_id: str, src: pd.DataFrame, syn: pd.DataFrame, num: list[str], cat: list[str],
                   dcr_min: float, min_cell_distance: float | None = None) -> list[Violation]:
    """Rows with DCR < dcr_min. With `min_cell_distance`, a row whose every numeric cell already moved
    >= that share from its source cell is not a near-duplicate (E-46): range-normalised DCR calls a
    small row of a wide-range statement 'close' even after a 30% move (live run 2026-10-26)."""
    if not num and not cat:
        return []
    nearest: list[int] = []
    d = dcr(src, syn, num, cat, nearest=nearest)
    # the cell rule is checked against the SAME row when rows keep their identity, else the closest one
    same = len(src) == len(syn)
    bad = [int(i) for i in np.flatnonzero(d < dcr_min)
           if min_cell_distance is None
           or not _far_in_every_cell(src, syn, num, int(i), int(i) if same else nearest[int(i)],
                                     min_cell_distance)]
    if not bad:
        return []
    return [Violation(check="dcr", severity=Severity.ERROR, table_id=table_id, rows=bad, repairable=True,
                      message=f"{len(bad)} generated rows are near-duplicates of a source row (DCR < {dcr_min})")]


def identical_match_share(src: pd.DataFrame, syn: pd.DataFrame, cols: list[str],
                          numeric: list[str] | None = None) -> float:
    """Share of generated rows that copy a source row. With `numeric`, rows that hold no number (a
    section heading such as 'Regulated Equity' with blank cells) are structure, not records."""
    if not cols or not len(syn):
        return 0.0
    if numeric:
        has = lambda f: f[numeric].apply(pd.to_numeric, errors="coerce").notna().any(axis=1)  # noqa: E731
        src, syn = src[has(src)], syn[has(syn)]
        if not len(syn):
            return 0.0
    s = set(map(tuple, src[cols].astype(str).to_numpy()))
    return float(np.mean([tuple(r) in s for r in syn[cols].astype(str).to_numpy()]))
