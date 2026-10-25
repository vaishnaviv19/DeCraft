# src/core/missingness.py
"""Missing Kinds and Missingness Patterns (M1.5). Missing is never zero except a confirmed dash."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DASHES = {"-", "--", chr(8212), chr(8211)}
NA_TOKENS = {"n/a", "na", "n.a.", "not applicable"}
NM_TOKENS = {"nm", "n.m.", "not meaningful"}


def token_kind(v) -> str | None:
    """None = a real value; otherwise 'null', 'dash', 'na' or 'nm'."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "null"
    t = str(v).strip().lower()
    if t == "":
        return "null"
    if t in DASHES:
        return "dash"
    if t in NA_TOKENS:
        return "na"
    if t in NM_TOKENS:
        return "nm"
    return None


@dataclass
class MissingProfile:
    rate: float
    kind: str                       # dominant token kind
    pattern: str                    # 'none' | 'leading' | 'grouped' | 'random'
    leading: int = 0
    n_source: int = 0
    group_col: str | None = None
    group_rates: dict = field(default_factory=dict)


def profile_missing(df: pd.DataFrame, col: str, group_cols=(), min_group: int = 3) -> MissingProfile:
    kinds = df[col].map(token_kind)
    miss = kinds.notna()
    n, rate = len(df), float(miss.mean())
    if rate == 0:
        return MissingProfile(0.0, "null", "none", n_source=n)
    kind = kinds[miss].mode().iloc[0]
    lead = int(np.argmin(miss.to_numpy())) if (~miss).any() else n
    if lead > 0 and not miss.iloc[lead:].any():
        return MissingProfile(rate, kind, "leading", lead, n)
    for g in group_cols:
        rates = miss.groupby(df[g].astype(str)).agg(["mean", "size"])
        rates = rates[rates["size"] >= min_group]
        if len(rates) >= 2 and rates["mean"].max() - rates["mean"].min() >= 0.5:
            return MissingProfile(rate, kind, "grouped", 0, n, g, rates["mean"].to_dict())
    return MissingProfile(rate, kind, "random", 0, n)


def dash_means_zero(values: list, links: dict[int, list[int]], rel_tol: float = 0.005) -> bool:
    """True when totals only reconcile if dash cells count as 0 (uses verify_links from hierarchy)."""
    from src.core.hierarchy import verify_links
    as_none = [None if token_kind(v) else float(v) for v in values]
    as_zero = [0.0 if token_kind(v) == "dash" else (None if token_kind(v) else float(v)) for v in values]
    r0, _ = verify_links(as_none, links, rel_tol)
    r1, _ = verify_links(as_zero, links, rel_tol)
    return (r1 or 0) >= 0.95 and (r1 or 0) > (r0 or 0)


TOKEN_TEXT = {"null": "", "dash": "-", "na": "N/A", "nm": "n.m."}


def apply_missingness(series: pd.Series, prof: MissingProfile, groups: pd.Series | None,
                      rng, kind_text: dict | None = None) -> pd.Series:
    """Write the Missingness Pattern back into a Display Table after totals are computed."""
    out = series.astype(object).copy()
    text = (kind_text or TOKEN_TEXT)[prof.kind]
    n = len(out)
    if prof.pattern == "leading":
        k = int(round(prof.leading / max(prof.n_source, 1) * n))
        out.iloc[:k] = text
    elif prof.pattern == "grouped" and groups is not None:
        p = groups.astype(str).map(prof.group_rates).fillna(prof.rate).to_numpy(float)
        out[rng.random(n) < p] = text
    elif prof.pattern == "random":
        out[rng.random(n) < prof.rate] = text
    return out
