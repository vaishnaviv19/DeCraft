# src/narrative/_templates.py
"""Deterministic section templates (M8.3). Used when the LLM is off or its draft is rejected.

Sentences are built from the section's own facts: which line items rose or fell and by how much,
which item was largest, totals and averages. Python chooses the verb from the sign of the change, so
the words always agree with the numbers. Phrasings rotate between sections so the report does not
repeat itself; general commentary only fills the remaining words."""
from __future__ import annotations

from src.narrative._writer import movement

COMMENTARY = [
    "Management attributes the result to steady execution across the portfolio.",
    "The mix of activities remained broadly consistent with earlier reporting periods.",
    "Working capital and cost discipline stayed in focus throughout the year.",
    "The figures below set out the main components behind these movements.",
    "Comparisons are made on the same basis as in the prior presentation.",
    "Operating conditions were mixed, with some segments advancing faster than others.",
    "Capital allocation continued to favour projects with clear returns.",
    "The board reviewed these results against the plan set at the start of the period.",
    "Seasonal effects are reflected in the timing of several line items.",
    "Further detail on each component is provided in the accompanying table.",
    "Risk management practices were applied consistently across reporting units.",
    "The outlook takes into account current demand and the pipeline of commitments.",
    "Pricing and volume both contributed to the movements described here.",
    "Overheads were monitored closely against the approved budget.",
    "These results are presented after the usual internal review and reconciliation.",
    "Investment in capacity is expected to support the next phase of growth.",
    "The relationship between revenue and costs is discussed alongside the table.",
    "Exposure to external factors was managed within the agreed limits.",
    "The performance of individual items is summarised in the chart that follows.",
    "Liquidity remained adequate to meet the commitments of the period.",
    "Each figure is shown on the same basis as the related table.",
    "The narrative below highlights the items that moved most during the period.",
    "Efficiency measures introduced earlier began to show in the results.",
    "Management continues to track these indicators on a regular basis.",
]

GROWTH = ["{label} {verb} by {growth} between {first} and {last}, closing at {latest}.",
          "Over the period from {first} to {last}, {label} {verb} by {growth} to reach {latest}.",
          "{label} ended {last} at {latest}, having {verb_pp} by {growth} since {first}."]
LARGEST = ["{label} recorded the highest {name}, at {value}.",
           "The largest {name} was reported for {label}, amounting to {value}.",
           "Among all items, {label} stood out with {name} of {value}."]
TOTAL = ["In total, {name} came to {value}.", "Across the table, {name} amounted to {value}.",
         "Taken together, {name} reached {value}."]
AVERAGE = ["{name} averaged {value} across the items shown.", "On average, {name} stood at {value}.",
           "The typical level of {name} was {value}."]
SHARE = ["That item accounted for {share} of the total.", "This represented {share} of the overall figure."]
PAST = {"rose": "risen", "fell": "fallen"}


def p(key: str, spec: str = "") -> str:
    return "{{ " + key + ("|" + spec if spec else "") + " }}"


def _fact_sentences(table_ids: list[str], facts: dict, turn: int) -> list[str]:
    out = []
    for table_id in table_ids:
        first, last = f"{table_id}.first_period", f"{table_id}.last_period"
        for key in [k for k in facts if k.startswith(f"{table_id}.") and k.endswith(".growth")]:
            base = key.rsplit(".", 1)[0]
            verb = movement(key, facts)
            label_key = f"{base}.label" if f"{base}.label" in facts else f"{base}.name"
            latest_key = f"{base}.latest" if f"{base}.latest" in facts else f"{base}.largest"
            if verb is None or label_key not in facts or latest_key not in facts or first not in facts or last not in facts:
                continue
            out.append(GROWTH[(turn + len(out)) % len(GROWTH)].format(
                label=p(label_key, "text"), verb=verb, verb_pp=PAST[verb], growth=p(key, "%:1|abs"),
                first=p(first, "text"), last=p(last, "text"), latest=p(latest_key)))
        for key in [k for k in facts if k.startswith(f"{table_id}.") and k.endswith(".largest_label")]:
            base = key.rsplit(".", 1)[0]
            if f"{base}.name" not in facts or f"{base}.largest" not in facts:
                continue
            out.append(LARGEST[(turn + len(out)) % len(LARGEST)].format(
                label=p(key, "text"), name=p(f"{base}.name", "text"), value=p(f"{base}.largest")))
            if f"{base}.largest_share" in facts:
                out.append(SHARE[(turn + len(out)) % len(SHARE)].format(share=p(f"{base}.largest_share", "%:1")))
            for suffix, pool in (("total", TOTAL), ("average", AVERAGE)):
                if f"{base}.{suffix}" in facts:
                    out.append(pool[(turn + len(out)) % len(pool)].format(
                        name=p(f"{base}.name", "text"), value=p(f"{base}.{suffix}")))
    return out


def section_template(section: dict, facts: dict, index: int = 0) -> str:
    table_ids = [t.get("table_id") for t in section.get("tables", []) if isinstance(t, dict)]
    if not table_ids:
        table_ids = sorted({k.split(".", 1)[0] for k in facts})
    sentences = _fact_sentences([t for t in table_ids if t], facts, index)
    target = int(section.get("target_word_count") or 150)
    words = sum(len(s.split()) for s in sentences)
    k = 0
    # A section's body budget can exceed the pool's word count (a disclaimer page has many boxes and no
    # facts). The pool therefore cycles until the target is met, with a moving start so two long
    # sections do not repeat the same order (M8.9, M8.12 words_per_page).
    while words < target:
        sentence = COMMENTARY[(index * 5 + k) % len(COMMENTARY)]
        sentences.insert(min(len(sentences), 1 + 2 * k), sentence)
        words += len(sentence.split())
        k += 1
        if k > 10 * len(COMMENTARY):                # defensive: target is finite
            break
    paragraphs = [" ".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)]
    return "\n\n".join(paragraphs)


def section_text(section: dict, facts: dict, resolve) -> str:
    return resolve(section_template(section, facts), facts)
