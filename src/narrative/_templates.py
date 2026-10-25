# src/narrative/_templates.py
"""Deterministic section templates (M8.3). Used when the LLM is off or its draft is rejected."""
from __future__ import annotations


def _format_for(fact) -> str:
    if fact.kind == "word":
        return "word"
    if fact.kind == "text":
        return "text"
    if getattr(fact, "unit", None) == "%":
        return "%:1"
    return ""


def section_template(section: dict, facts: dict) -> str:
    title = str(section.get("title") or "Overview")
    table_ids = [t.get("table_id") for t in section.get("tables", []) if isinstance(t, dict)]
    keys = [k for k in facts if table_ids and any(k.startswith(f"{tid}.") for tid in table_ids)]
    if not keys:
        keys = list(facts)
    keys = keys[:10]

    sentences = ["This section reviews the figures that support the report."]
    for key in keys:
        fact = facts[key]
        spec = _format_for(fact)
        placeholder = "{{ " + key + ("|" + spec if spec else "") + " }}"
        if fact.kind == "word":
            sentences.append(f"The direction of the series is {placeholder}, and it is read together "
                             f"with the figures below.")
        elif fact.kind == "text":
            sentences.append(f"The most relevant item in this part of the report is {placeholder}.")
        elif getattr(fact, "unit", None) == "%":
            sentences.append(f"The associated ratio is {placeholder}, which the commentary compares "
                             f"with the prior period.")
        else:
            sentences.append(f"The reported amount is {placeholder}, and it is the figure used in "
                             f"the discussion that follows.")
    sentences.append("The commentary keeps to the figures shown in the tables and charts, so every "
                     "statement can be checked against the data on the page.")
    sentences.append("Where a measure changed, the change is described in words and the underlying "
                     "numbers remain visible in the accompanying table.")
    sentences.append("Readers who need more detail can turn to the tables, which carry the same values "
                     "at the displayed precision.")
    return " ".join(sentences)


def section_text(section: dict, facts: dict, resolve) -> str:
    return resolve(section_template(section, facts), facts)
