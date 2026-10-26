# src/narrative/_repeats.py   (v3.3 F-11: repetition across parallel-written sections)
"""Sentences in different sections that say nearly the same thing. Similarity = Jaccard of word bigrams after
replacing placeholders with their key (the same fact cited twice counts as the same words)."""
from __future__ import annotations

import re

PH = re.compile(r"[{][{]\s*([a-z0-9_.]+)[^}]*[}][}]", re.I)


def _bigrams(s: str) -> set[tuple[str, str]]:
    w = re.findall(r"[a-z0-9_.]+", PH.sub(lambda m: " " + m.group(1).lower() + " ", s.lower()))
    return set(zip(w, w[1:]))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if len(s.split()) >= 6]


def cross_section_repeats(sections: dict[str, str], threshold: float = 0.5) -> list[tuple[str, str, str, str, float]]:
    """-> (section_a, sentence_a, section_b, sentence_b, similarity) for pairs in DIFFERENT sections."""
    items = [(sid, s, _bigrams(s)) for sid, text in sections.items() for s in sentences(text)]
    hits = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a[0] == b[0] or not a[2] or not b[2]:
                continue
            sim = len(a[2] & b[2]) / len(a[2] | b[2])
            if sim >= threshold:
                hits.append((a[0], a[1], b[0], b[1], round(sim, 2)))
    return hits


def drop_sentence(text: str, sentence: str) -> str:
    """`text` without `sentence` (whitespace tidied)."""
    out = text.replace(sentence, "", 1)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\s+([.,;])", r"\1", out)).strip()
