# src/inference/_profile.py
"""Field Profiles (M1.6): physical type, Semantic Type, unit, Scale, Decimals, missing kinds and statistics.

Values are parsed into Base Units; the Raw Cell strings are never changed. A column whose format is
mixed keeps a low `llm_confidence` and raises a warning instead of guessing.
"""
from __future__ import annotations

import re
from collections import Counter

from src.config import Config
from src.contracts import (CategoryStats, DateStats, Evidence, FieldProfile,
                           MissingKind, NumericStats, SourceTable, TableQuality)
from src.core.missingness import token_kind
from src.core.periods import parse_period
from src.core.units import ScaleInfo, currency_code, parse_scale, resolve_scale
from src.core.values import displayed_decimals, parse_date, parse_number

CURRENCY_WORD_RE = re.compile(r"\b(usd|inr|eur|gbp)\b|rs\.?|\$|€|£|₹", re.I)
CURRENCY_HEADER_RE = re.compile(
    r"\b(revenue|sales|cost|profit|ebitda|income|expense|capex|debt|cash|price|amount|value|"
    r"dividend|turnover|market cap|enterprise value)\b", re.I)
PERCENTAGE_HEADER_RE = re.compile(r"%|\b(growth|margin|yield|rate|percent|market share)\b", re.I)
COUNT_HEADER_RE = re.compile(
    r"\b(units?|count|qty|quantity|number of|volume|employees|headcount|shares|no\.)\b", re.I)
IDENTIFIER_HEADER_RE = re.compile(r"\b(id|code|ref|reference|no\.?|number|sku|isin|ticker)\b", re.I)
IDENTIFIER_VALUE_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._/-]{2,}$")
RATIO_HEADER_RE = re.compile(r"\b(multiple|ratio|p/e|p/bv|ev/ebitda)\b|%", re.I)
BOOLEAN_VALUES = {"yes", "no", "true", "false", "y", "n"}

MAX_CATEGORIES = 50


def _resolve_scale(sources: list[tuple[str, str]]) -> tuple[ScaleInfo, str, list[str]]:
    """Resolve Scale and report which source (header, caption, context_text) supplied it."""
    scale, warns = resolve_scale([text for _, text in sources])
    if scale.multiplier == 1.0:
        return scale, "none", warns
    for name, text in sources:
        parsed = parse_scale(text)
        if parsed.multiplier == scale.multiplier:
            return scale, name, warns
    return scale, "none", warns


def _semantic_type(header: str, values: list[str], physical: str, numbers: list[float],
                   n_unique: int, identifier: bool) -> tuple[str, list[Evidence]]:
    header = header or ""
    evidence: list[Evidence] = []
    if physical in {"date", "period"}:
        evidence.append(Evidence(source="cell_format", detail=f"values parse as {physical}"))
        return physical, evidence
    if PERCENTAGE_HEADER_RE.search(header):
        evidence.append(Evidence(source="header", detail="percentage column"))
        return "percentage", evidence
    if physical in {"integer", "float"}:
        if any("%" in v for v in values):
            evidence.append(Evidence(source="cell_format", detail="percentage sign in values"))
            return "percentage", evidence
        if CURRENCY_WORD_RE.search(header) or any(CURRENCY_WORD_RE.search(v) for v in values) \
                or CURRENCY_HEADER_RE.search(header):
            evidence.append(Evidence(source="cell_format" if any(CURRENCY_WORD_RE.search(v) for v in values) else "header",
                                    detail="currency amount"))
            return "currency", evidence
        if physical == "integer" and COUNT_HEADER_RE.search(header):
            evidence.append(Evidence(source="header", detail="count column"))
            return "count", evidence
        evidence.append(Evidence(source="cell_format", detail="numeric values without a currency unit"))
        return "numeric", evidence
    if identifier:
        evidence.append(Evidence(source="cell_format", detail="unique, identifier-shaped values"))
        return "identifier", evidence
    if n_unique and n_unique <= max(10, 0.5 * len(values)):
        evidence.append(Evidence(source="cell_format", detail="few distinct text values"))
        return "category", evidence
    return "unknown", evidence


def _identifier_like(header: str, values: list[str], n_non_missing: int, n_unique: int) -> bool:
    if n_non_missing < 3 or n_unique / n_non_missing < 0.95:
        return False
    if IDENTIFIER_HEADER_RE.search(header or ""):
        return True
    matches = sum(bool(IDENTIFIER_VALUE_RE.match(v)) for v in values)
    return matches / n_non_missing >= 0.6


def _mixed_format(values: list[str]) -> bool:
    """True when most values are numbers but some are neither numbers, dates, periods nor booleans."""
    numeric = sum(parse_number(v) is not None for v in values)
    if not numeric or numeric / len(values) < 0.5:
        return False
    return any(parse_number(v) is None and parse_date(v) is None and parse_period(v) is None
               and v.strip().lower() not in BOOLEAN_VALUES for v in values)


def _physical_type(values: list[str], numbers: list[float | None], all_missing: bool) -> str:
    if all_missing:
        return "empty"
    if all(n is not None for n in numbers):
        no_dots = all("." not in v.replace(",", "") for v in values)
        return "integer" if no_dots and all(float(n).is_integer() for n in numbers) else "float"
    if all(parse_date(v) is not None for v in values):
        return "date"
    if all(parse_period(v) is not None for v in values):
        return "period"
    if all(v.strip().lower() in BOOLEAN_VALUES for v in values):
        return "boolean"
    return "text"


def profile_table(table: SourceTable, quality: TableQuality, cfg: Config) -> tuple[list[FieldProfile], list[str]]:
    """One FieldProfile per column, plus the warnings the Inference Report must carry."""
    profiles: list[FieldProfile] = []
    warnings: list[str] = []
    n = len(table.raw_cells)

    # One Scale per Source Table: the unit must be stated by the table itself (any header cell) or
    # by its caption. Page prose is never trusted, because one page can hold tables in different units.
    header_text = " | ".join(table.header)
    table_scale, table_scale_source, table_warns = _resolve_scale(
        [("header", header_text), ("caption", table.caption or "")])
    table_currency = currency_code(header_text) or currency_code(table.caption or "")
    warnings.extend(f"{table.table_id}: {w}" for w in table_warns)

    for j, header in enumerate(table.header):
        header = header or ""
        raw = [row[j] if j < len(row) else None for row in table.raw_cells]
        kinds = [token_kind(v) for v in raw]
        missing = [k for k in kinds if k is not None]
        values = [str(v).strip() for v, k in zip(raw, kinds) if k is None]
        numbers = [parse_number(v) for v in values]
        all_missing = not values

        semantic_header = "" if re.match(r"^Unnamed: \d+$", header) else header
        n_non_missing = len(values)
        n_unique = len(set(values))
        physical = _physical_type(values, numbers, all_missing)
        identifier = physical not in {"date", "period", "boolean"} and _identifier_like(
            semantic_header, values, n_non_missing, n_unique)
        semantic, evidence = _semantic_type(
            semantic_header, values, physical, [x for x in numbers if x is not None], n_unique, identifier)

        unit = None
        if semantic == "percentage":
            unit = "%"
            if any("bps" in v.lower() for v in values):
                unit = "bps"
        elif semantic == "currency":
            unit = next((currency_code(v) for v in values if currency_code(v)), None) \
                or currency_code(header)

        # Scale is parsed and formatted only; percentages, counts and text keep their value.
        scale, scale_source = table_scale, table_scale_source
        ratio_column = bool(RATIO_HEADER_RE.search(header)) or any(v.lower().endswith("x") for v in values)
        if semantic == "numeric" and scale.multiplier != 1.0 and not ratio_column:
            code = table_currency or currency_code(scale.evidence)
            if code:
                semantic, unit = "currency", code
        if semantic in {"percentage", "count", "identifier", "date", "period", "boolean",
                        "text", "unknown", "category"} or ratio_column:
            scale = ScaleInfo()
            scale_source = "none"
        if scale.multiplier != 1.0:
            evidence.append(Evidence(source=scale_source if scale_source != "none" else "header",
                                     detail=f"scale {scale.multiplier:g} from {scale.evidence!r}"))

        decimals = None
        if physical in {"integer", "float"}:
            counted = [d for d in (displayed_decimals(v) for v in values) if d is not None]
            decimals = Counter(counted).most_common(1)[0][0] if counted else None

        numeric_stats = None
        if numbers and any(x is not None for x in numbers):
            scaled = [x * scale.multiplier for x in numbers if x is not None]
            numeric_stats = NumericStats(
                minimum=min(scaled), maximum=max(scaled), mean=sum(scaled) / len(scaled),
                quantiles={"p25": _quantile(scaled, 0.25), "p50": _quantile(scaled, 0.50),
                           "p75": _quantile(scaled, 0.75)})

        dates = None
        if physical == "date" and values:
            parsed = sorted(d for d in (parse_date(v) for v in values) if d is not None)
            dates = DateStats(earliest=parsed[0], latest=parsed[-1])

        categories = None
        if physical in {"text", "boolean", "period"} and values and n_unique <= max(MAX_CATEGORIES, 0.5 * n_non_missing):
            categories = CategoryStats(frequencies=dict(Counter(values).most_common(MAX_CATEGORIES)))

        mixed = bool(values) and _mixed_format(values)
        llm_confidence = None
        if mixed:
            failed = sum(parse_number(v) is None for v in values)
            llm_confidence = min(0.5, cfg.thresholds.llm_confidence_escalate - 0.1)
            warnings.append(
                f"{table.table_id}.{header}: mixed formats, {failed} of {len(values)} "
                f"values did not parse as numbers")

        evidence.append(Evidence(source="extraction", detail=f"table grade {quality.grade.value}"))
        profiles.append(FieldProfile(
            table_id=table.table_id, column=header, physical_type=physical, semantic_type=semantic,
            unit=unit, scale=scale.multiplier, decimals=decimals, n=n, n_missing=len(missing),
            n_unique=n_unique,
            missing_kinds={MissingKind(k): missing.count(k) for k in sorted(set(missing))},
            numeric=numeric_stats, dates=dates, categories=categories,
            extraction_grade=quality.grade, llm_confidence=llm_confidence, evidence=evidence))
    return profiles, warnings


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
