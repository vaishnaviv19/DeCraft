# src/generation/_grid.py  -- E-51: number grids that read like a real statement
"""Every number cell of a table that keeps its rows (statements, fact sheets, valuation tables) is
generated here, so the fake tables are as consistent as real ones.

Relations are found in the SOURCE by Python and kept only when they hold on every testable cell
within the rounding of the printed numbers (nothing is assumed about the domain, no model decides):

  eq         the same quantity printed twice: Sales FY23A on page 1 = Revenue FY23A in the P&L;
             Total Assets = Total Liabilities
  sum        a row = signed sum of rows of its table, column by column (EBIT = EBITDA - Depreciation,
             Total = sum of the shares); a '-' component counts as zero. A total that is the same in
             every column (shares adding to 100.0) stays fixed and its parts are rescaled to it
  growth     a row = % change of another row over the previous period
  colgrowth  a column = % change between two columns, row by row ('YoY Growth %', 'Change %');
             rows printed in bps are the difference x 100 (850bps), point rows the difference
  ratio      a row = k * a / b, k in {1, 100} (margins, tax rate, EPS = PAT / shares)

Base cells (no relation) move by one document factor x one factor per series x a small wobble per
cell, at least `min_value_distance` from the source, rounded to the cell's own printed decimals.
Derived cells are then computed from those ROUNDED inputs in dependency order and rounded to their
own decimals, so whatever a reader re-adds or re-divides on the page agrees with what is printed.
"""
from __future__ import annotations

import functools
import itertools
import math
import re
from dataclasses import dataclass, field

import numpy as np

from src.core.missingness import token_kind
from src.core.periods import parse_period
from src.core.values import displayed_decimals, parse_number

Key = tuple[str, int, int]                   # (table_id, row, column) in the source's raw cells

KS = (1.0, 100.0)                            # ratio multipliers: plain ratio, percentage
MAX_WINDOW = 16                              # longest run of rows a total may add up
COMBO_ROWS = 12                              # nearest rows a signed sum may combine
MAX_TERMS = 4
MIN_INFO_RATIO = 4.5                         # sum of log10(|v| / half-unit): ratio of two rows of its own table
MIN_INFO_CROSS = 6.5                         # ... with an input from another table
MIN_INFO_SUM = 4.0
WOBBLE = 0.015                               # per cell: small, so a derived row keeps its shape
ROW_SPREAD = 0.04                            # per series around the document factor
HEADLINE = 0.10                              # D-13: a row's headline value moves >= 10%


# ---------------------------------------------------------------- grids
@dataclass
class Grid:
    table_id: str
    header: list[str]
    raw: list[list[str]]
    label_col: int
    cells: dict[tuple[int, int], tuple[float, int]] = field(default_factory=dict)   # value, decimals
    dashes: set[tuple[int, int]] = field(default_factory=set)                      # '-' = nil
    tokens: dict[tuple[int, int], str] = field(default_factory=dict)               # period token
    subheaders: set[int] = field(default_factory=set)
    percent: set[tuple[int, int]] = field(default_factory=set)
    ordinals: set[int] = field(default_factory=set)                                # row-number columns


_NUMBERISH = re.compile(r"^[\s(+\-]*(?:rs\.?|inr|usd|[$€£₹])?\s*[+\-]?\d[\d,]*(?:\.\d+)?\s*(?:%|x|bps)?\s*\)?$",
                        re.I)


def _is_number_cell(text: str) -> bool:
    return bool(_NUMBERISH.match(text or "")) and parse_number(text) is not None


def _norm_token(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).upper()


def build_grid(table) -> Grid | None:
    """The number grid of one Source Table, or None when it has no row-label column."""
    raw = [[str(v) for v in row] for row in table.raw_cells]
    header = [str(h) for h in table.header]
    n_cols = max([len(header)] + [len(row) for row in raw])
    if not raw or n_cols < 2:
        return None
    label_col = None
    for c in range(n_cols):
        values = [row[c].strip() for row in raw if c < len(row) and row[c].strip()]
        if len(values) >= 2 and sum(_is_number_cell(v) for v in values) <= 0.3 * len(values):
            label_col = c
            break
    if label_col is None:
        return None
    grid = Grid(table.table_id, header, raw, label_col)
    current = {c: (header[c] if c < len(header) and not re.fullmatch(r"Unnamed: \d+", header[c]) else "")
               for c in range(n_cols)}
    for r, row in enumerate(raw):
        others = [(c, row[c].strip()) for c in range(len(row)) if c != label_col and row[c].strip()]
        if others and not any(_is_number_cell(v) for _, v in others) \
                and all(token_kind(v) is None for _, v in others):
            grid.subheaders.add(r)                          # 'Shareholding % | Q3FY23 | Q4FY23 | Q1FY24'
            for c, v in others:
                current[c] = v
            continue
        for c, v in others:
            if _is_number_cell(v):
                grid.cells[(r, c)] = (float(parse_number(v)), displayed_decimals(v) or 0)
                grid.tokens[(r, c)] = _norm_token(current.get(c, ""))
                if "%" in v or "%" in current.get(c, "") or "%" in (row[label_col] if label_col < len(row) else ""):
                    grid.percent.add((r, c))
            elif token_kind(v) == "dash":
                grid.dashes.add((r, c))
    # a column numbering the rows (Sr. No. 1, 2, 3 ...) is an ordinal, not data: it stays as printed
    for c in sorted({c for _, c in grid.cells}):
        column = sorted((r, v) for (r, cc), (v, d) in grid.cells.items() if cc == c)
        values = [v for _, v in column]
        if len(values) >= 3 and values == [float(i) for i in range(int(values[0]), int(values[0]) + len(values))] \
                and values[0] in (0.0, 1.0):
            grid.ordinals.add(c)
            for r, _ in column:
                grid.cells.pop((r, c), None)
                grid.tokens.pop((r, c), None)
    return grid if len(grid.cells) >= 4 else None


def _half(d: int) -> float:
    return 0.5 * 10.0 ** -d


def _info(value: float, d: int) -> float:
    return math.log10(max(abs(value) / _half(d), 1.0))


# ---------------------------------------------------------------- relations
@dataclass
class Relation:
    kind: str                       # 'sum' | 'ratio' | 'growth' | 'bps' | 'diff' | 'plug'
    out: Key
    terms: list[tuple[float, Key]]  # sum/plug: (sign, cell); ratio/growth/bps/diff: (1, a), (1, b)
    k: float = 1.0
    fixed: bool = False             # sum whose total is the same in every column (kept as printed)


class Relations:
    """All relations of one document, found on the source grids."""

    def __init__(self, grids: dict[str, Grid]):
        self.grids = grids
        self.value: dict[Key, tuple[float, int]] = {}
        self.dash: set[Key] = set()
        for t, g in grids.items():
            for (r, c), vd in g.cells.items():
                self.value[(t, r, c)] = vd
            self.dash |= {(t, r, c) for r, c in g.dashes}
        self.parent: dict[Key, Key] = {}
        self.rel: dict[Key, Relation] = {}        # out cell -> its relation
        self.row_links: dict[tuple[str, int], set[tuple[str, int]]] = {}
        self.col_derived: set[tuple[str, int]] = set()

    # -- union-find over cells (the 'eq' relation)
    def find(self, key: Key) -> Key:
        while self.parent.get(key, key) != key:
            self.parent[key] = self.parent.get(self.parent[key], self.parent[key])
            key = self.parent[key]
        return key

    def union(self, a: Key, b: Key) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def derived(self, key: Key) -> bool:
        return key in self.rel

    def interval(self, key: Key) -> tuple[float, float]:
        if key in self.dash:
            return 0.0, 0.0
        v, d = self.value[key]
        return v - _half(d), v + _half(d)


def _row_cells(g: Grid, r: int) -> dict[int, tuple[float, int]]:
    return {c: vd for (rr, c), vd in g.cells.items() if rr == r}


def _precision(rel: Relations, key: Key) -> float:
    """|value| / half a printed unit: 1,141 -> 2282, 0.3 -> 6. Dashes count as exact."""
    if key in rel.dash:
        return math.inf
    v, d = rel.value[key]
    return abs(v) / _half(d)


def find_equalities(rel: Relations) -> None:
    """Cells that print the same quantity: rows of two tables that agree on aligned period columns
    (>= 2 cells with 3+ significant digits, or >= 3 cells and no disagreement), and rows of one table
    that agree column by column (Total Assets = Total Liabilities)."""
    by_row: dict[tuple[str, int], dict[str, Key]] = {}
    for (t, r, c) in rel.value:
        token = rel.grids[t].tokens.get((r, c), "")
        if token and parse_period(token) is not None:
            by_row.setdefault((t, r), {})[token] = (t, r, c)
    rows = list(by_row)
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a[0] != b[0]:
                _maybe_link(rel, [(by_row[a][tok], by_row[b][tok]) for tok in by_row[a] if tok in by_row[b]], a, b)
    for t, g in rel.grids.items():
        by_r: dict[int, dict[int, Key]] = {}
        for (r, c) in g.cells:
            if (t, c) not in rel.col_derived:
                by_r.setdefault(r, {})[c] = (t, r, c)
        row_ids = sorted(by_r)
        for i, ra in enumerate(row_ids):
            for rb in row_ids[i + 1:]:
                pairs = [(by_r[ra][c], by_r[rb][c]) for c in by_r[ra] if c in by_r[rb]]
                _maybe_link(rel, pairs, (t, ra), (t, rb))


def _maybe_link(rel: Relations, pairs: list[tuple[Key, Key]], a, b) -> None:
    if len(pairs) < 2:
        return
    equal = [(x, y) for x, y in pairs if rel.value[x] == rel.value[y] and rel.value[x][0] != 0]
    strong = [p for p in equal if _info(*rel.value[p[0]]) >= 2.0]
    mismatched = len(pairs) - len(equal)
    if len(strong) >= 2 or (len(equal) >= 3 and not mismatched
                            and all(_info(*rel.value[x]) >= 1.0 for x, _ in equal)):
        for x, y in equal:
            rel.union(x, y)
        # a linked pair one printed unit apart is rounding (Total Liabilities 10,95,031 / Total Assets
        # 10,95,032, second sample): left unlinked, the two totals drifted 1,436 apart in the decoy
        for x, y in pairs:
            (vx, dx), (vy, dy) = rel.value[x], rel.value[y]
            if (x, y) not in equal and abs(vx - vy) <= 1.0001 * 10.0 ** -max(dx, dy) \
                    and _info(vx, dx) >= 4.5 and _info(vy, dy) >= 4.5:
                rel.union(x, y)
        rel.row_links.setdefault(a, set()).add(b)
        rel.row_links.setdefault(b, set()).add(a)


def find_stated_facts(rel: Relations, text: str) -> None:
    """One figure printed in two tables and written in the text is one fact: the target price 301 is
    the SOTP total AND the last entry of the rating history ('target price of Rs. 301'). Generated
    apart they disagreed (252 vs 261). Only a value with 3+ significant digits, in exactly two cells of
    different tables, and stated in the document's own text is linked."""
    if not text:
        return
    cells: dict[tuple[float, int], list[Key]] = {}
    for key, (value, decimals) in rel.value.items():
        if value != 0 and abs(value) * 10 ** decimals >= 100:
            cells.setdefault((value, decimals), []).append(key)
    for (value, decimals), keys in cells.items():
        if len(keys) != 2 or keys[0][0] == keys[1][0] or rel.find(keys[0]) == rel.find(keys[1]):
            continue
        plain = f"{abs(value):.{decimals}f}"
        grouped = f"{abs(value):,.{decimals}f}"
        if re.search(r"(?<![\d.,])(?:" + re.escape(plain) + "|" + re.escape(grouped) + r")(?![\d]|[.,]\d)", text):
            rel.union(keys[0], keys[1])


def _matches(lo: float, hi: float, target: tuple[float, float], slack: float = 1e-9) -> bool:
    return lo <= target[1] + slack and hi >= target[0] - slack


def _ratio_bounds(a: tuple[float, float], b: tuple[float, float], k: float) -> tuple[float, float] | None:
    if b[0] <= 0 <= b[1]:
        return None
    values = [k * x / y for x in a for y in b]
    return min(values), max(values)


MIN_INPUT_PRECISION = 100.0      # inputs of a ratio / growth: at least 3 significant printed digits
NEAR_INPUT_PRECISION = 20.0      # ... or 2 for the row directly above (EPS -> its own % change row)
MIN_TARGET_PRECISION = 20.0      # a sum's printed total


def find_column_growth(rel: Relations) -> None:
    """A column that is the change between two other columns, row by row (YoY / QoQ / Change %)."""
    for t, g in rel.grids.items():
        columns = sorted({c for _, c in g.cells})
        rows = sorted({r for r, _ in g.cells})
        for c in columns:
            best = None
            for a, b in itertools.permutations([x for x in columns if x != c], 2):
                modes: dict[int, str] = {}
                testable = 0
                for r in rows:
                    keys = [(t, r, c), (t, r, a), (t, r, b)]
                    if any(k not in rel.value for k in keys):
                        continue
                    testable += 1
                    if min(_precision(rel, keys[1]), _precision(rel, keys[2])) < NEAR_INPUT_PRECISION:
                        continue
                    target = rel.interval(keys[0])
                    ia, ib = rel.interval(keys[1]), rel.interval(keys[2])
                    growth = _ratio_bounds(ia, ib, 100.0)
                    if growth and _matches(growth[0] - 100, growth[1] - 100, target):
                        modes[r] = "growth"
                    elif _matches(100 * (ia[0] - ib[1]), 100 * (ia[1] - ib[0]), target):
                        modes[r] = "bps"
                    elif _matches(ia[0] - ib[1], ia[1] - ib[0], target) and g.cells[(r, c)][0] != 0:
                        modes[r] = "diff"
                if len(modes) >= 3 and len(modes) >= 0.6 * testable \
                        and sum(m == "growth" for m in modes.values()) >= 2:
                    score = (len(modes), -abs(a - c) - abs(b - c))
                    if best is None or score > best[0]:
                        best = (score, a, b, modes)
            if best is None:
                continue
            _, a, b, modes = best
            rel.col_derived.add((t, c))
            for r, mode in modes.items():
                rel.rel[(t, r, c)] = Relation(mode, (t, r, c), [(1.0, (t, r, a)), (1.0, (t, r, b))])


def find_column_sums(rel: Relations) -> None:
    """A column that is the sum or difference of two other columns on every row (Total cost =
    Units + Cost): holds on every row with all three numbers, at least three of them."""
    for t, g in rel.grids.items():
        columns = sorted({c for _, c in g.cells})
        rows = sorted({r for r, _ in g.cells})
        for c in columns:
            if (t, c) in rel.col_derived:
                continue
            for a, b in itertools.combinations([x for x in columns if x != c and (t, x) not in rel.col_derived], 2):
                for sign in (1.0, -1.0):
                    tested, ok = 0, True
                    for r in rows:
                        keys = [(t, r, c), (t, r, a), (t, r, b)]
                        if any(k not in rel.value for k in keys):
                            continue
                        if _precision(rel, keys[0]) < MIN_TARGET_PRECISION:
                            ok = False
                            break
                        ia, ib, target = rel.interval(keys[1]), rel.interval(keys[2]), rel.interval(keys[0])
                        lo, hi = (ia[0] + ib[0], ia[1] + ib[1]) if sign > 0 else (ia[0] - ib[1], ia[1] - ib[0])
                        tested += 1
                        if not _matches(lo, hi, target):
                            ok = False
                            break
                    if ok and tested >= 3:
                        rel.col_derived.add((t, c))
                        for r in rows:
                            if all((t, r, x) in rel.value for x in (a, b, c)):
                                rel.rel[(t, r, c)] = Relation("sum", (t, r, c),
                                                              [(1.0, (t, r, a)), (sign, (t, r, b))])
                        break
                if (t, c) in rel.col_derived:
                    break


def _measure_cells(rel: Relations, g: Grid, r: int) -> dict[int, tuple[float, int]]:
    return {c: vd for c, vd in _row_cells(g, r).items() if (g.table_id, c) not in rel.col_derived}


def find_sums(rel: Relations) -> None:
    """Rows that add up rows of their table (totals, subtotals, signed sums such as EBIT)."""
    for t, g in rel.grids.items():
        rows = sorted({r for r, _ in g.cells})
        consumed: set[int] = set()
        sums: list[int] = []
        for r in rows:
            target = _measure_cells(rel, g, r)
            if not target or any(rel.derived((t, r, c)) for c in target):
                continue
            if any(_precision(rel, (t, r, c)) < MIN_TARGET_PRECISION for c in target):
                continue
            above = [x for x in rows if x < r]
            barrier = max([s for s in g.subheaders if s < r], default=-1)
            block = [x for x in above if x > barrier]
            found = _window_sum(rel, g, r, target, [x for x in block if x not in consumed], block)
            if found is None and len(target) >= 2:
                pool = list(dict.fromkeys(block[-8:] + [s for s in sums if s > barrier]))[-COMBO_ROWS:]
                found = _signed_sum(rel, g, r, target, pool)
            if found is None:
                continue
            terms, fixed = found
            for c in target:
                rel.rel[(t, r, c)] = Relation("sum", (t, r, c), [(s, (t, x, c)) for s, x in terms], fixed=fixed)
            consumed |= {x for _, x in terms}
            sums.append(r)


def _term_interval(rel: Relations, key: Key) -> tuple[float, float] | None:
    if key in rel.dash:
        return 0.0, 0.0
    return rel.interval(key) if key in rel.value else None


def _sum_check(rel, t, terms, cols, r) -> list[float] | None:
    """Relative tolerance per column when the signed sum holds in every column, else None. A '-'
    component is zero; at least one column must add two printed numbers."""
    tolerances, pairs = [], 0
    for c in cols:
        lo = hi = 0.0
        present = 0
        for sign, x in terms:
            iv = _term_interval(rel, (t, x, c))
            if iv is None:
                return None
            present += (t, x, c) in rel.value
            lo, hi = (lo + iv[0], hi + iv[1]) if sign > 0 else (lo - iv[1], hi - iv[0])
        if present == 0 or not _matches(lo, hi, rel.interval((t, r, c))):
            return None
        pairs += present >= 2
        v, d = rel.value[(t, r, c)]
        tolerances.append((hi - lo + 2 * _half(d)) / 2 / max(abs(v), 1e-12))
    return tolerances if pairs else None


def _window_sum(rel, g, r, target, candidates, block):
    """All +1: the run of (not yet used) rows directly above, optionally skipping one row inside it
    (Total Assets leaves out Gross Fixed Assets). A one-column total must add up EVERY number above
    it in its block (Target Price = sum of the per-share values)."""
    t = g.table_id
    usable = [x for x in candidates if any((x, c) in g.cells or (x, c) in g.dashes for c in target)]
    single = len(target) == 1
    for length in range(2, min(MAX_WINDOW, len(usable)) + 1):
        window = usable[-length:]
        options = [window] + ([[x for x in window if x != skip] for skip in window[1:-1]] if length >= 4 else [])
        for rows in options:
            if single:
                numbered = [x for x in block if (x, next(iter(target))) in g.cells]
                if len(rows) < 3 or rows != numbered:
                    continue
            tolerances = _sum_check(rel, t, [(1.0, x) for x in rows], target, r)
            if tolerances is None:
                continue
            limit = 0.03 if single else 0.02
            if float(np.median(tolerances)) <= limit and max(tolerances) <= 0.10:
                fixed = len(target) >= 2 and len({vd for vd in target.values()}) == 1
                return [(1.0, x) for x in rows], fixed
    return None


def _signed_sum(rel, g, r, target, pool):
    """Two to four of the nearby rows (and earlier subtotals) with signs: EBIT = EBITDA - Depreciation,
    PBT = EBIT - Interest + Other Income + Associates. Vectorised over the sign patterns."""
    t = g.table_id
    cols = sorted(target)
    pool = [x for x in pool if all((x, c) in g.cells or (x, c) in g.dashes for c in cols)
            and any((x, c) in g.cells and g.cells[(x, c)][0] != 0 for c in cols)
            and all(_precision(rel, (t, x, c)) >= NEAR_INPUT_PRECISION for c in cols if (x, c) in g.cells)]
    if len(pool) < 2:
        return None
    lo = np.array([[_term_interval(rel, (t, x, c))[0] for c in cols] for x in pool])
    hi = np.array([[_term_interval(rel, (t, x, c))[1] for c in cols] for x in pool])
    tlo = np.array([rel.interval((t, r, c))[0] for c in cols])
    thi = np.array([rel.interval((t, r, c))[1] for c in cols])
    tval = np.array([target[c][0] for c in cols])
    best = None
    for size in range(2, MAX_TERMS + 1):
        signs = np.array(list(itertools.product((1.0, -1.0), repeat=size)))
        pos = signs[:, :, None] > 0
        for rows in itertools.combinations(range(len(pool)), size):
            low, high = lo[list(rows)], hi[list(rows)]
            s_lo = np.where(pos, low[None], -high[None]).sum(axis=1)
            s_hi = np.where(pos, high[None], -low[None]).sum(axis=1)
            ok = ((s_lo <= thi + 1e-9) & (s_hi >= tlo - 1e-9)).all(axis=1)
            if not ok.any():
                continue
            for i in np.nonzero(ok)[0]:
                tol = ((s_hi[i] - s_lo[i]) + (thi - tlo)) / 2 / np.maximum(np.abs(tval), 1e-12)
                if np.median(tol) > 0.015 or tol.max() > 0.10:
                    continue
                error = float(np.abs((s_lo[i] + s_hi[i]) / 2 - tval).sum())
                if best is None or error < best[0]:
                    best = (error, [(float(signs[i][j]), pool[rows[j]]) for j in range(size)])
        if best is not None:
            return best[1], False
    return None


def _ext(rel: Relations, t: str, r: int) -> dict[str, Key]:
    """Period token -> cell for one row, completed by rows that print the same series elsewhere."""
    cache = rel.__dict__.setdefault("_ext_cache", {})
    if (t, r) in cache:
        return cache[(t, r)]
    out: dict[str, Key] = {}
    for (tt, rr) in [(t, r)] + sorted(rel.row_links.get((t, r), ())):
        for (r2, c) in rel.grids[tt].cells:
            if r2 == rr:
                token = rel.grids[tt].tokens.get((r2, c), "")
                if token and parse_period(token) is not None:
                    out.setdefault(token, (tt, r2, c))
    cache[(t, r)] = out
    return out


@functools.lru_cache(maxsize=4096)
def _period_key(token: str):
    p = parse_period(token)
    return (p.kind, p.year, p.sub) if p else None


def _row_precision(rel: Relations, keys) -> float:
    return min(_precision(rel, k) for k in keys)


def find_growth(rel: Relations) -> None:
    """Rows that are the % change of another row over the previous period. The change is the least
    precise of the three numbers (a % change is never more exact than the amounts it compares)."""
    candidates = [(t, r) for t, g in rel.grids.items() for r in sorted({rr for rr, _ in g.cells})]
    for t, g in rel.grids.items():
        for r in sorted({rr for rr, _ in g.cells}):
            mine = {tok: key for tok, key in _ext(rel, t, r).items() if key[0] == t and key[1] == r
                    and not rel.derived(key)}
            if len(mine) < 2:
                continue
            top = max(_precision(rel, key) for key in mine.values())
            same = [cand for cand in candidates if cand[0] == t and cand[1] != r]
            same.sort(key=lambda cand: (cand[1] > r, abs(cand[1] - r)))
            others = [cand for cand in candidates if cand[0] != t]
            for cand in same + others:
                floor = NEAR_INPUT_PRECISION if cand == (t, r - 1) else MIN_INPUT_PRECISION
                series = _ext(rel, *cand)
                ordered = sorted((k for k in series if _period_key(k)), key=_period_key)
                matched, tested = {}, 0
                for tok, key in mine.items():
                    if tok not in series or _period_key(tok) is None:
                        continue
                    earlier = [k for k in ordered if _period_key(k)[0] == _period_key(tok)[0]
                               and _period_key(k) < _period_key(tok)]
                    if not earlier:
                        continue
                    prev, now = series[earlier[-1]], series[tok]
                    if now in rel.dash or prev in rel.dash:
                        continue
                    tested += 1
                    if min(_precision(rel, now), _precision(rel, prev)) < max(floor, top):
                        tested = -1
                        break
                    bounds = _ratio_bounds(rel.interval(now), rel.interval(prev), 100.0)
                    if bounds and _matches(bounds[0] - 100, bounds[1] - 100, rel.interval(key)):
                        matched[key] = (now, prev)
                if tested >= 2 and len(matched) == tested:
                    for key, (now, prev) in matched.items():
                        rel.rel[key] = Relation("growth", key, [(1.0, now), (1.0, prev)])
                    break


def find_ratios(rel: Relations) -> None:
    """Rows that are k * a / b of two other rows (same table first, then rows of other tables aligned
    on the period columns). The ratio is the least precise of the three rows and both inputs carry
    at least three significant printed digits."""
    rows = [(t, r) for t, g in rel.grids.items() for r in sorted({rr for rr, _ in g.cells})]
    for t, g in rel.grids.items():
        for r in sorted({rr for rr, _ in g.cells}):
            target = {c: vd for c, vd in _measure_cells(rel, g, r).items() if not rel.derived((t, r, c))}
            if len(target) < 2 or sum(_info(*vd) for vd in target.values()) < MIN_INFO_RATIO:
                continue
            columns = sorted(target)
            top = max(_precision(rel, (t, r, c)) for c in columns)
            tokens = {c: g.tokens.get((r, c), "") for c in columns}
            aligned: list[tuple[tuple[str, int], list[Key]]] = []
            for cand in rows:
                if cand == (t, r):
                    continue
                if cand[0] == t:
                    keys = [(t, cand[1], c) for c in columns]
                elif all(tokens[c] and parse_period(tokens[c]) is not None for c in columns):
                    series = _ext(rel, *cand)
                    if not all(tokens[c] in series for c in columns):
                        continue
                    keys = [series[tokens[c]] for c in columns]
                else:
                    continue
                if all(k in rel.value for k in keys) and \
                        _row_precision(rel, keys) >= max(MIN_INPUT_PRECISION, top):
                    aligned.append((cand, keys))
            if len(aligned) < 2:
                continue
            lo = np.array([[rel.interval(k)[0] for k in keys] for _, keys in aligned])
            hi = np.array([[rel.interval(k)[1] for k in keys] for _, keys in aligned])
            tlo = np.array([rel.interval((t, r, c))[0] for c in columns])
            thi = np.array([rel.interval((t, r, c))[1] for c in columns])
            valid_b = (lo > 0) | (hi < 0)
            best = None
            for k in KS:
                with np.errstate(divide="ignore", invalid="ignore"):
                    q = np.stack([k * lo[:, None, :] / lo[None, :, :], k * lo[:, None, :] / hi[None, :, :],
                                  k * hi[:, None, :] / lo[None, :, :], k * hi[:, None, :] / hi[None, :, :]])
                qmin, qmax = q.min(axis=0), q.max(axis=0)
                ok = (qmin <= thi + 1e-9) & (qmax >= tlo - 1e-9) & valid_b[None, :, :]
                ok = ok.all(axis=2)
                np.fill_diagonal(ok, False)
                info = sum(_info(*vd) for vd in target.values())
                for ia, ib in zip(*np.nonzero(ok)):
                    if rel.find(aligned[ia][1][0]) == rel.find(aligned[ib][1][0]):
                        continue
                    same_table = (aligned[ia][0][0] == t) + (aligned[ib][0][0] == t)
                    # thousands of cross-table pairs: a short, imprecise row matches some pair by chance
                    # (P/B 2.1, 2.8, 2.5 = Depreciation / Tax). Inputs from other tables need more evidence
                    if same_table < 2 and info < MIN_INFO_CROSS:
                        continue
                    mid = (qmin[ia, ib] + qmax[ia, ib]) / 2
                    error = float((np.abs(mid - (tlo + thi) / 2)
                                   / np.maximum(np.abs(tlo + thi) / 2, 1e-12)).sum())
                    score = (same_table, -error)
                    if best is None or score > best[0]:
                        best = (score, k, aligned[ia][1], aligned[ib][1])
            if best is None:
                continue
            _, k, a_keys, b_keys = best
            for c, a, b in zip(columns, a_keys, b_keys):
                rel.rel[(t, r, c)] = Relation("ratio", (t, r, c), [(1.0, a), (1.0, b)], k=k)


def detect(grids: dict[str, Grid], text: str = "") -> Relations:
    rel = Relations(grids)
    find_column_growth(rel)                  # first: change columns never make two rows 'equal'
    find_column_sums(rel)
    find_equalities(rel)
    find_stated_facts(rel, text)
    find_sums(rel)
    find_growth(rel)
    find_ratios(rel)
    _resolve(rel)
    return rel


def _resolve(rel: Relations) -> None:
    """One definition per equality class. A second SUM over the same class becomes a plug on its
    largest free part (Total Liabilities = Total Assets: one liability absorbs the difference); any
    other second definition is dropped (the class value comes from the first). Cycles are cut."""
    by_class: dict[Key, list[Relation]] = {}
    for key, relation in rel.rel.items():
        by_class.setdefault(rel.find(key), []).append(relation)
    final: dict[Key, Relation] = {}
    defined = set(by_class)
    for root, relations in by_class.items():
        relations.sort(key=lambda x: (x.kind != "sum", x.out))
        first = relations[0]
        final[first.out] = first
        for extra in relations[1:]:
            if extra.kind != "sum" or first.kind != "sum":
                continue
            free = [(s, x) for s, x in extra.terms
                    if x in rel.value and rel.find(x) not in defined and rel.value[x][0] != 0
                    and sum(1 for k in rel.value if rel.find(k) == rel.find(x)) == 1]
            if not free:
                continue
            sign, plug = max(free, key=lambda item: abs(rel.value[item[1]][0]))
            terms = [(sign, first.out)] + [(-sign * s, x) for s, x in extra.terms if x != plug]
            final[plug] = Relation("plug", plug, terms)
            defined.add(rel.find(plug))
    rel.rel = final
    # cut cycles (a relation whose inputs reach back to its own class)
    while True:
        cycle = _first_cycle(rel)
        if cycle is None:
            break
        rel.rel.pop(cycle, None)


def _inputs(relation: Relation) -> list[Key]:
    return [x for _, x in relation.terms]


def _first_cycle(rel: Relations) -> Key | None:
    graph = {rel.find(out): {rel.find(x) for x in _inputs(r)} for out, r in rel.rel.items()}
    state: dict[Key, int] = {}
    for start in graph:
        if state.get(start):
            continue
        stack = [(start, iter(graph.get(start, ())))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:
                return next(out for out in rel.rel if rel.find(out) == node)
            if nxt in graph and not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(graph[nxt])))
    return None


# ---------------------------------------------------------------- generation
def _round(value: float, d: int) -> float:
    from src.core.rounding import display_round
    return display_round(value, d)


def _away(new: float, old: float, d: int, distance: float, up: bool) -> float:
    """`new` rounded to `d` decimals and at least `distance` (relative) from `old`; when rounding
    lands too close, move one printed unit at a time away from `old` (bigger if `up`). A move down
    that would reach zero or flip the sign goes up instead (face value 1 never becomes 0)."""
    out = _round(new, d)
    if old == 0 or (abs(out - old) / abs(old) >= distance and out != 0 and (out > 0) == (old > 0)):
        return out
    sign = 1.0 if old > 0 else -1.0
    unit = 10.0 ** -d
    for direction in ((up, not up) if up else (False, True)):
        out = _round(sign * abs(old) * (1 + distance * 1.05 if direction else 1 - distance * 1.05), d)
        while abs(out - old) / abs(old) < distance:
            out = _round(out + sign * (unit if direction else -unit), d)
        if out != 0 and (out > 0) == (old > 0):
            return out
    return _round(old + sign * max(unit, abs(old) * distance * 1.05), d)


def generate_numbers(rel: Relations, rng: np.random.Generator, distance: float,
                     scale_range: tuple[float, float] = (0.80, 1.30)) -> dict[Key, float]:
    """New value for every source number cell of the grids: base cells scaled, derived recomputed."""
    roots = {}
    for key in rel.value:
        roots.setdefault(rel.find(key), []).append(key)
    # series = rows joined by printed equalities; one factor each (trends and row ratios move together)
    series_of: dict[tuple[str, int], tuple[str, int]] = {}
    for (t, r) in {(k[0], k[1]) for k in rel.value}:
        seen, todo = {(t, r)}, [(t, r)]
        while todo:
            node = todo.pop()
            for nxt in rel.row_links.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        series_of[(t, r)] = min(seen)
    # periods DOWN the side (Year | Revenue): a series is a column, not a row
    # ... and so is a list of records with one or two number columns (a dated price history)
    down = {t for t, g in rel.grids.items()
            if sum(parse_period(str(row[g.label_col]).strip()) is not None
                   for row in g.raw if g.label_col < len(row) and str(row[g.label_col]).strip())
            >= max(2, 0.6 * len(g.raw))
            or (len({c for _, c in g.cells}) <= 2 and len({r for r, _ in g.cells}) >= 4)}

    def series_key(key: Key):
        return (key[0], "column", key[2]) if key[0] in down else series_of[(key[0], key[1])]
    low, high = scale_range
    doc = float(rng.uniform(low, high))
    while abs(doc - 1) < 0.15:
        doc = float(rng.uniform(low, high))
    factors: dict[tuple[str, int], float] = {}

    def factor(series) -> float:
        if series not in factors:
            for _ in range(50):
                f = doc * float(rng.uniform(1 - ROW_SPREAD, 1 + ROW_SPREAD))
                if abs(f - 1) >= max(distance, HEADLINE) + WOBBLE + 0.02:
                    break
            factors[series] = f
        return factors[series]

    derived_roots = {rel.find(k) for k in rel.rel}
    fixed_parts = {x for r in rel.rel.values() if r.kind == "sum" and r.fixed for _, x in r.terms}
    values: dict[Key, float] = {}
    for root, members in roots.items():
        if root in derived_roots:
            continue
        key = members[0]
        v, d = rel.value[key]
        row_values = [rel.value[(key[0], key[1], c)][0] for (rr, c) in rel.grids[key[0]].cells if rr == key[1]]
        constant = len(set(row_values)) == 1 and len(row_values) > 1
        if key in fixed_parts:
            continue                                  # set by _normalise_fixed below
        f = factor(series_key(key))
        w = 1.0 if constant else float(rng.uniform(1 - WOBBLE, 1 + WOBBLE))
        new = v * f * w
        if any((m[1], m[2]) in rel.grids[m[0]].percent for m in members) and 0 <= v <= 100:
            new = v if v == 100 else min(new, 99.0 if d == 0 else 100 - 10.0 ** -d)
        d_max = max(rel.value[m][1] for m in members)
        values[root] = v if (v == 100 and new == v) else _away(new, v, d_max, distance, f >= 1)
    _keep_order(rel, values, series_key, distance)
    _normalise_fixed(rel, values, rng, distance)
    base = dict(values)
    _evaluate(rel, values)
    _headline_totals(rel, values, base, distance)
    _no_exact_repeats(rel, values, base)
    out: dict[Key, float] = {}
    for key in rel.value:
        out[key] = _round(values[rel.find(key)], rel.value[key][1])
    return out


def _keep_order(rel: Relations, values: dict, series_key, distance: float) -> None:
    """The per-cell wobble may swap two close neighbours of a series (200 and 210 became 212 and 208):
    the new values of each series are re-assigned in the source's order, so every up and down stays.
    A swap that would bring a cell within `distance` of its source is not made."""
    groups: dict = {}
    for root in values:
        groups.setdefault(series_key(root), []).append(root)
    for roots in groups.values():
        if len(roots) < 3:
            continue
        by_source = sorted(roots, key=lambda k: rel.value[k][0])
        new_sorted = sorted(values[k] for k in roots)
        trial = dict(zip(by_source, new_sorted))
        ok = all(rel.value[k][0] == 0 or abs(trial[k] - rel.value[k][0]) / abs(rel.value[k][0]) >= distance
                 for k in roots)
        if ok and all((trial[k] > 0) == (rel.value[k][0] > 0) or rel.value[k][0] == 0 for k in roots):
            values.update(trial)


def _normalise_fixed(rel: Relations, values: dict, rng, distance: float) -> None:
    """Parts of a fixed total (shares adding to 100.0): one factor per part ROW (a holding moves
    smoothly over the quarters), rescaled to the total per column with largest-remainder rounding.
    The best of several draws is kept: the one with the fewest parts within `distance` of the source."""
    from src.core.rounding import largest_remainder
    groups: dict[tuple, list[Relation]] = {}
    for relation in rel.rel.values():
        if relation.kind == "sum" and relation.fixed and all(s > 0 for s, _ in relation.terms):
            rows = tuple(sorted({(x[0], x[1]) for _, x in relation.terms}))
            groups.setdefault(rows, []).append(relation)
    for rows, relations in groups.items():
        best = None
        for _ in range(40):
            row_factor = {row: float(rng.uniform(0.6, 1.6)) for row in rows}
            trial, close = {}, 0
            for relation in relations:
                total, d = rel.value[relation.out]
                parts = [x for _, x in relation.terms if x in rel.value]
                raw = [rel.value[x][0] * row_factor[(x[0], x[1])] * float(rng.uniform(0.98, 1.02))
                       for x in parts]
                current = sum(raw)
                if current <= 0:
                    continue
                rounded = largest_remainder([v / current * total for v in raw], total, d)
                for x, v in zip(parts, rounded):
                    trial[rel.find(x)] = v
                    old = rel.value[x][0]
                    close += old != 0 and abs(v - old) / abs(old) < distance
            if best is None or close < best[0]:
                best = (close, trial)
            if close == 0:
                break
        if best is not None:
            values.update(best[1])
    for root in [rel.find(x) for r in rel.rel.values() if r.kind == "sum" and r.fixed
                 for _, x in r.terms if x in rel.value]:
        values.setdefault(root, rel.value[root][0])


STRUCTURAL = {100.0}


def _repeats(rel: Relations, values: dict) -> list[Key]:
    """Derived cells that print exactly their source value (task 6: nothing real may survive)."""
    out = []
    for out_key, relation in rel.rel.items():
        if relation.fixed:
            continue
        for member in [k for k in rel.value if rel.find(k) == rel.find(out_key)]:
            v, d = rel.value[member]
            if v != 0 and v not in STRUCTURAL and _round(values[rel.find(member)], d) == v:
                out.append(out_key)
                break
    return out


def _constant_row(rel: Relations, key: Key) -> bool:
    row = [vd[0] for (r, c), vd in rel.grids[key[0]].cells.items() if r == key[1]]
    return len(row) > 1 and len(set(row)) == 1


def _base_inputs(rel: Relations, key: Key, base: dict) -> list[Key]:
    seen, todo, found = set(), [key], []
    while todo:
        node = todo.pop()
        relation = rel.rel.get(node) or next((r for o, r in rel.rel.items() if rel.find(o) == rel.find(node)), None)
        for _, x in (relation.terms if relation else []):
            root = rel.find(x)
            if root in seen or x in rel.dash:
                continue
            seen.add(root)
            if root in base:
                found.append(root)
            else:
                todo.append(x)
    return found


def _latest_cells(rel: Relations) -> list[Key]:
    """Per row, the cell of its latest period (what the headline fact 'latest' reads)."""
    best: dict[tuple[str, int], tuple] = {}
    for (t, r, c) in rel.value:
        key = _period_key(rel.grids[t].tokens.get((r, c), "") or "")
        if key is None:
            continue
        if (t, r) not in best or key > best[(t, r)][0]:
            best[(t, r)] = (key, (t, r, c))
    return [cell for _, cell in best.values()]


def _headline_totals(rel: Relations, values: dict, base: dict, distance: float, passes: int = 6) -> None:
    """D-13 for recomputed totals: the latest value of a summed row moves >= HEADLINE from the source.
    A net figure of mixed signs (cash flow from investing) can land near its source even though every
    part moved; the largest part that can take the difference (same sign, still >= `distance` from its
    own source) absorbs it."""
    for _ in range(passes):
        changed = False
        for cell in _latest_cells(rel):
            relation = rel.rel.get(cell)
            if relation is None or relation.kind not in ("sum", "plug") or relation.fixed:
                continue
            old = rel.value[cell][0]
            new = values[rel.find(cell)]
            if old == 0 or abs(new - old) / abs(old) >= HEADLINE:
                continue
            side = 1.0 if new >= old else -1.0
            want = old * (1 + side * (HEADLINE + 0.03)) if old > 0 else old * (1 - side * (HEADLINE + 0.03))
            delta = want - new
            for sign, term in sorted(relation.terms, key=lambda st: -abs(rel.value.get(st[1], (0, 0))[0])):
                root = rel.find(term)
                if root not in base or _constant_row(rel, root) or term in rel.dash:
                    continue
                src_v, d = rel.value[root]
                moved = _round(base[root] + delta / sign, d)
                if src_v == 0 or moved == 0 or (moved > 0) != (src_v > 0) \
                        or abs(moved - src_v) / abs(src_v) < distance:
                    continue
                base[root] = moved
                changed = True
                break
        if not changed:
            return
        values.clear()
        values.update(base)
        _evaluate(rel, values)


def _no_exact_repeats(rel: Relations, values: dict, base: dict, passes: int = 10) -> None:
    """Nudge one base input (+/-2%, rounded) of each derived cell that repeats its source value."""
    for attempt in range(passes):
        repeats = _repeats(rel, values)
        if not repeats:
            return
        for key in repeats:
            inputs = [i for i in _base_inputs(rel, key, base) if not _constant_row(rel, i)]
            if not inputs:
                continue
            root = inputs[attempt % len(inputs)]
            v, d = rel.value[root]
            step = max(10.0 ** -d, abs(base[root]) * 0.02)
            base[root] = _round(base[root] + (step if attempt % 2 == 0 else -step), d)
        values.clear()
        values.update(base)
        _evaluate(rel, values)


def _evaluate(rel: Relations, values: dict) -> None:
    pending = {rel.find(out): r for out, r in rel.rel.items()}
    while pending:
        progressed = False
        for root, relation in list(pending.items()):
            inputs = [rel.find(x) for x in _inputs(relation) if x not in rel.dash]
            if any(i in pending or i not in values for i in inputs):
                continue
            values[root] = _apply(relation, values, rel)
            pending.pop(root)
            progressed = True
        if not progressed:                       # unresolved inputs: fall back to scaled source
            for root, relation in pending.items():
                values[root] = rel.value[relation.out][0]
            break


def _val(key: Key, values: dict, rel: Relations) -> float:
    if key in rel.dash:
        return 0.0
    v = values[rel.find(key)]
    return _round(v, max(rel.value[m][1] for m in [key]))


def _apply(relation: Relation, values: dict, rel: Relations) -> float:
    d = rel.value[relation.out][1]
    if relation.kind in ("sum", "plug"):
        if relation.fixed:
            return rel.value[relation.out][0]
        return _round(sum(s * _val(x, values, rel) for s, x in relation.terms), d)
    a, b = (_val(x, values, rel) for _, x in relation.terms)
    if relation.kind == "ratio":
        return _round(relation.k * a / b, d) if b else 0.0
    if relation.kind == "growth":
        return _round(100.0 * (a / b - 1.0), d) if b else 0.0
    if relation.kind == "bps":
        return _round(100.0 * (a - b), d)
    if relation.kind == "diff":
        return _round(a - b, d)
    raise ValueError(relation.kind)


# ---------------------------------------------------------------- shared entry points
_CACHE: dict[tuple, tuple[dict[str, Grid], Relations]] = {}


def grids_of(doc) -> dict[str, Grid]:
    out = {}
    for table in doc.tables:
        grid = build_grid(table)
        if grid is not None:
            out[table.table_id] = grid
    return out


def relations_for(doc) -> tuple[dict[str, Grid], Relations]:
    """Grids and relations of a Source Document, detected once per document (positions do not
    change when periods are shifted, so later calls reuse the first detection)."""
    key = (doc.document_sha256, tuple(sorted(t.table_id for t in doc.tables)))
    if key not in _CACHE:
        grids = grids_of(doc)
        _CACHE[key] = (grids, detect(grids, " ".join(doc.text_blocks)))
    return _CACHE[key]


def derived_positions(doc) -> dict[str, set[tuple[int, int]]]:
    """table_id -> {(row, column index)} of cells computed by a relation (exempt from distance)."""
    _, rel = relations_for(doc)
    out: dict[str, set[tuple[int, int]]] = {}
    for key in rel.value:
        if rel.find(key) in {rel.find(k) for k in rel.rel} or key in rel.rel:
            out.setdefault(key[0], set()).add((key[1], key[2]))
    return out


def number_style(grid: Grid) -> dict:
    """How a table writes its numbers: bracket negatives, thousands separators."""
    texts = [grid.raw[r][c] for (r, c) in grid.cells]
    from src.core.values import is_indian_grouping
    return {"brackets": any(t.strip().startswith("(") for t in texts),
            "commas": any("," in t for t in texts),
            "indian": any(is_indian_grouping(t) for t in texts)}


def is_indian(text: str) -> bool:
    from src.core.values import is_indian_grouping
    return is_indian_grouping(text)


def format_cell(template: str, value: float, style: dict | None = None) -> str:
    """`value` printed the way the source cell `template` prints its number, with the table's
    negative style and thousands separators (a template '884' still prints 1,141 in a comma table)."""
    from src.core.values import format_number_like
    style = style or {}
    text = str(template).strip()
    if style.get("commas") and "," not in text and abs(value) >= 1000:
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m:                                   # a comma-style placeholder with the same decimals
            frac = m.group(0).partition(".")[2]
            placeholder = "1,00,000" if style.get("indian") else "1,000"
            text = text[:m.start()] + placeholder + (f".{frac}" if frac else "") + text[m.end():]
    elif style.get("indian") and "," in text and not is_indian(text) and abs(value) >= 100000:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", text)          # '1,370' in a lakh-grouped table
        if m:
            frac = m.group(0).partition(".")[2]
            text = text[:m.start()] + "1,00,000" + (f".{frac}" if frac else "") + text[m.end():]
    bracketed = text.startswith("(") and text.endswith(")")
    if value < 0 and style.get("brackets") and not bracketed and not text.lstrip().startswith("-"):
        return "(" + format_number_like(text, abs(value)) + ")"
    if value >= 0 and bracketed:
        return format_number_like(text[1:-1], value)
    return format_number_like(text, value)


def column_decimals(table, columns: list[str]) -> dict[str, int]:
    """Most decimals any number cell of each column prints (a statement mixes 32,703 and 12.2 in one
    column; rounding the whole column to 0 decimals printed every % change as a whole number)."""
    from src.core.values import displayed_decimals, parse_number
    out: dict[str, int] = {}
    for j, column in enumerate(columns):
        places = [displayed_decimals(row[j]) or 0 for row in table.raw_cells
                  if j < len(row) and parse_number(row[j]) is not None]
        if places:
            out[column] = max(places)
    return out


RATIO_KINDS = {"ratio", "growth", "bps", "diff"}


def ratio_row_labels(doc) -> dict[str, set[str]]:
    """table_id -> labels (lower case) of rows whose numbers are recomputed ratios / changes
    (margins, EPS, tax rate, % change). They are not levels: a 10% headline distance on them would
    contradict the moved amounts they are computed from."""
    grids, rel = relations_for(doc)
    out: dict[str, set[str]] = {}
    for (t, r, c), relation in rel.rel.items():
        if relation.kind in RATIO_KINDS:
            g = grids[t]
            label = g.raw[r][g.label_col] if g.label_col < len(g.raw[r]) else ""
            out.setdefault(t, set()).add(" ".join(str(label).split()).lower())
    return out
