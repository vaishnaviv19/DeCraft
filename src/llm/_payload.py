# src/llm/_payload.py
"""Compact prompt payloads (decision E-05). Rules: minified JSON, short keys explained once in the system
prompt, no values the model does not need, and a hard input-token cap enforced by dropping the LOWEST-priority
optional fields first - never by truncating text mid-way."""
import json
import math


def estimate_tokens(text: str) -> int:
    """Conservative estimate (~3.5 chars/token for English+JSON). Real usage from the API replaces it."""
    return math.ceil(len(text) / 3.5)


def compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def fit_payload(required: dict, optional: list[tuple[str, object]], max_tokens: int) -> tuple[str, list[str]]:
    """required: always sent. optional: (key, value) in DESCENDING priority; dropped from the end until the
    payload fits. Raises ValueError if the required part alone is too large (split the task instead)."""
    payload = dict(required)
    if estimate_tokens(compact(payload)) > max_tokens:
        raise ValueError("required payload exceeds budget; split the call")
    dropped = []
    for key, value in optional:
        trial = dict(payload, **{key: value})
        if estimate_tokens(compact(trial)) <= max_tokens:
            payload = trial
        else:
            dropped.append(key)
    return compact(payload), dropped


def batch_items(items: list[dict], max_tokens: int, overhead_tokens: int = 0) -> list[list[dict]]:
    """Greedy batching (e.g. several tables per Annotator call) under a token cap. An item that alone exceeds
    the cap goes in a batch of its own (the caller must trim it, e.g. fewer sample rows)."""
    batches, cur, cur_tok = [], [], overhead_tokens
    for it in items:
        t = estimate_tokens(compact(it))
        if cur and cur_tok + t > max_tokens:
            batches.append(cur)
            cur, cur_tok = [], overhead_tokens
        cur.append(it)
        cur_tok += t
    if cur:
        batches.append(cur)
    return batches
