# src/rendering/_fit_doc.py
"""Box fitting (M8.9.2, E-14): every text block must fit its box before rendering.

A block that does not fit goes to the `shorten` prompt with a word target derived from the overflow
(at most 2 tries). A shorter draft is accepted only if it keeps every placeholder, keeps every
capitalised name of the original, adds no digits and fits. Otherwise the text is cut at the last
full sentence that fits and the block is flagged for QA. Font sizes never change."""
from __future__ import annotations

import json
import math
import re

from src.contracts.report import ShortenOut
from src.llm import LLMGateway, estimate_tokens
from src.narrative import SHORTEN, illegal_digits, placeholder_keys
from src.rendering._fit import Box, fits

TRIES = 2
_NAME = re.compile(r"\b[A-Z][\w&'’.-]+(?:\s+[A-Z][\w&'’.-]+)+\b")


def names_in(text: str) -> set[str]:
    return {match.group(0) for match in _NAME.finditer(text)}


def sentence_cut(text: str, box: Box, width_fn) -> str:
    """The last full sentence prefix that fits (trimming words only when the first sentence is too long)."""
    kept = ""
    for sentence in [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part]:
        trial = f"{kept} {sentence}".strip()
        if fits(trial, box, width_fn)[0]:
            kept = trial
            continue
        if kept:
            break
        words = sentence.split()
        while words and not fits(" ".join(words), box, width_fn)[0]:
            words.pop()
        kept = " ".join(words)
        break
    return kept


def _keeps_everything(original: str, candidate: str, allowed) -> list[str]:
    errors = []
    missing = set(placeholder_keys(original)) - set(placeholder_keys(candidate))
    if missing:
        errors.append(f"drops placeholders {sorted(missing)[:4]}")
    lowered = candidate.lower()
    gone = {name for name in names_in(original) if name.lower() not in lowered}
    if gone:
        errors.append(f"drops names {sorted(gone)[:3]}")
    digits = illegal_digits(candidate, set(allowed))
    if digits:
        errors.append(f"adds digits {digits[:4]}")
    return errors


def fit_blocks(texts: dict[str, str], boxes: dict[str, Box], width_fns: dict[str, object],
               cfg, gateway: LLMGateway | None, periods: list[str]
               ) -> tuple[dict[str, str], list[dict], dict]:
    """Returns the fitted texts, one row per block, and the overflow/shortened/cut summary."""
    rows: list[dict] = []
    flags: list[str] = []
    overflow_before = shortened = cut = 0
    enabled = gateway is not None and getattr(gateway, "enabled", False)
    for block_id, text in texts.items():
        box, width_fn = boxes.get(block_id), width_fns.get(block_id)
        if box is None or width_fn is None:
            continue
        ok, need, have = fits(text, box, width_fn)
        if not ok:
            overflow_before += 1
        current = text
        for _try in range(TRIES if not ok and enabled else 0):
            _, need_now, have = fits(current, box, width_fn)
            words = len(current.split())
            target = max(1, math.floor(words * have / max(1, need_now) * 0.95))
            payload = json.dumps({"text": current, "max_words": target})
            # The `shorten` prompt's own cap is 1,500 tokens. A block larger than that cannot be sent
            # without truncating the text before the model sees it, so use the deterministic sentence
            # cut instead (E-14's fallback), rather than turning a local size problem into a Run failure.
            if estimate_tokens(SHORTEN.system + payload) > SHORTEN.max_input_tokens:
                flags.append(f"{block_id}: too long for the shorten budget; cut at a sentence")
                break
            reply = gateway.ask_json(SHORTEN, payload, ShortenOut)
            if reply is None:
                break
            candidate = reply.text.strip()
            if _keeps_everything(text, candidate, periods):
                continue
            current = candidate
            if fits(candidate, box, width_fn)[0]:
                shortened += 1
                ok = True
                break
        if not ok and not fits(current, box, width_fn)[0]:
            current = sentence_cut(current, box, width_fn)
            cut += 1
            flags.append(f"{block_id}: cut to the last full sentence that fits")
        texts[block_id] = current
        final_ok, need, have = fits(current, box, width_fn)
        if not final_ok:
            flags.append(f"{block_id}: still does not fit after the cut")
        rows.append({"id": block_id, "need": need, "have": have, "fits": final_ok})
    return texts, rows, {"overflow_before": overflow_before, "shortened": shortened,
                         "cut": cut, "flags": flags}
