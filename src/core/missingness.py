# src/core/missingness.py
"""Missing Kinds and Missingness Patterns (M1.5). Missing is never zero except a confirmed dash."""
from __future__ import annotations


import numpy as np

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


def dash_means_zero(values: list, links: dict[int, list[int]], rel_tol: float = 0.005) -> bool:
    """True when totals only reconcile if dash cells count as 0 (uses verify_links from hierarchy)."""
    from src.core.hierarchy import verify_links
    as_none = [None if token_kind(v) else float(v) for v in values]
    as_zero = [0.0 if token_kind(v) == "dash" else (None if token_kind(v) else float(v)) for v in values]
    r0, _ = verify_links(as_none, links, rel_tol)
    r1, _ = verify_links(as_zero, links, rel_tol)
    return (r1 or 0) >= 0.95 and (r1 or 0) > (r0 or 0)


TOKEN_TEXT = {"null": "", "dash": "-", "na": "N/A", "nm": "n.m."}


