# src/narrative/_consistency.py
"""Consistency (M8.8): rename new ids, merge Claims, and reconcile conflicts with four guards (E-08, E-09).

Agents never talk to each other. Python merges what the writers returned, finds conflicts, and only a
conflict goes to the Reconciler. Every rewrite is checked before it may touch a paragraph."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from src.config import Config
from src.llm import LLMGateway
from src.narrative._claims import Claim, Conflict, Entity, check_resolution, merge_claims, norm
from src.narrative._prompts import RECONCILER
from src.narrative._tokens import illegal_digits


class RewriteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section_id: str = ""
    block_id: str
    paragraph: int = 0
    text: str


class ReconcileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: str
    reason: str = ""
    rewrites: list[RewriteOut] = Field(default_factory=list)


def rename_new(section_id: str, entities: list[Entity]) -> list[Entity]:
    """'N1' in section S4 becomes 'N:S4:1' so ids are unique across a parallel run."""
    return [Entity(id=f"N:{section_id}:{n}", kind=entity.kind, name=entity.name,
                   attrs=dict(entity.attrs), introduced_in=section_id)
            for n, entity in enumerate(entities, start=1)]


def merge(bible, claims: list[Claim], new_entities: list[Entity]):
    entities = {entity.id: Entity(entity.id, entity.kind, entity.name, dict(entity.attrs))
                for entity in bible.entities}
    return merge_claims(entities, claims, new_entities)


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def drop_sentences(text: str, values: tuple[str, ...]) -> tuple[str, int]:
    """Remove every sentence that states one of the conflicting values."""
    wanted = {norm(value) for value in values if value}
    kept, dropped = [], 0
    for sentence in _sentences(text):
        if any(value and value in norm(sentence) for value in wanted):
            dropped += 1
            continue
        kept.append(sentence)
    return " ".join(kept), dropped


def _capitalised_names(text: str) -> set[str]:
    return {" ".join(match.split()) for match in
            re.findall(r"\b(?:[A-Z][\w&'-]+(?:\s+(?:of|and|&|for)\s+)?[A-Z][\w&'-]*)\b", text)}


def rewrite_guards(conflict: Conflict, reply: ReconcileOut, texts: dict[str, str],
                   contexts: dict[str, str], allowed_names: set[str], periods: list[str],
                   facts: dict) -> list[str]:
    """Guards (a)-(d) from E-09; an empty list means the rewrites may be applied."""
    errors = []
    if not check_resolution(conflict, reply.choice):
        errors.append(f"choice {reply.choice!r} is not one of {list(conflict.values)}")
    allowed_blocks = set(contexts)
    for rewrite in reply.rewrites:
        if rewrite.block_id not in allowed_blocks:
            errors.append(f"rewrite touches an unflagged block {rewrite.block_id!r}")
            continue
        flagged = {claim.paragraph for claim in conflict.claims
                   if claim.block == rewrite.block_id}
        before = contexts.get(rewrite.block_id, "").split("\n\n")
        after = rewrite.text.split("\n\n")
        if len(before) != len(after):
            errors.append(f"{rewrite.block_id}: the rewrite changes the paragraph structure")
        else:
            touched = {index for index, (old, new) in enumerate(zip(before, after))
                       if old.strip() != new.strip()}
            if touched - flagged:
                errors.append(f"{rewrite.block_id}: the rewrite changes unflagged paragraph(s) "
                              f"{sorted(touched - flagged)}")
        if norm(reply.choice) not in norm(rewrite.text):
            errors.append(f"{rewrite.block_id}: the chosen value does not appear verbatim")
        bad = illegal_digits(rewrite.text, set(periods))
        if bad:
            errors.append(f"{rewrite.block_id}: digits not allowed: {bad[:5]}")
        from src.narrative._placeholders import placeholder_keys
        unknown = [key for key in placeholder_keys(rewrite.text) if key not in facts]
        if unknown:
            errors.append(f"{rewrite.block_id}: unknown placeholders {unknown[:5]}")
        new_names = {name for name in _capitalised_names(rewrite.text)
                     if name not in allowed_names and len(name.split()) > 1}
        if new_names:
            errors.append(f"{rewrite.block_id}: introduces new names {sorted(new_names)[:3]}")
    return errors


def reconcile(bible, conflicts: list[Conflict], texts: dict[str, str], cfg: Config,
              gateway: LLMGateway | None, periods: list[str], facts: dict,
              allowed_names: set[str]) -> tuple[object, dict[str, str], dict[str, int]]:
    """Reconcile every conflict (at most report.reconcile_rounds); unresolved sentences are dropped."""
    stats = {"conflicts_initial": len(conflicts), "resolved": 0, "dropped": 0,
             "final_conflicts": 0, "rounds": 0}
    queue = list(conflicts)
    for round_number in range(1, 1 + max(0, cfg.report.reconcile_rounds)):
        if not queue:
            break
        stats["rounds"] = round_number
        remaining: list[Conflict] = []
        for conflict in queue:
            contexts = {claim.block: texts.get(claim.block or "", "")
                        for claim in conflict.claims if claim.block}
            if gateway is None or not gateway.enabled:
                remaining.append(conflict)
                continue
            payload = {"candidates": list(conflict.values),
                       "entity": {"id": conflict.entity, "attr": conflict.attr},
                       "ctx": [{"sid": claim.section, "b": claim.block, "p": claim.paragraph,
                                "text": contexts.get(claim.block, "")}
                               for claim in conflict.claims],
                       "per": periods}
            reply = gateway.ask_json(RECONCILER, json.dumps(payload), ReconcileOut)
            if reply is None:
                remaining.append(conflict)
                continue
            errors = rewrite_guards(conflict, reply, texts, contexts, allowed_names,
                                    periods, facts)
            if errors:
                remaining.append(conflict)
                continue
            for rewrite in reply.rewrites:
                texts[rewrite.block_id] = rewrite.text
            entity = bible.get(conflict.entity)
            if entity is not None:
                if conflict.attr == "name":
                    entity.name = reply.choice
                else:
                    entity.attrs[conflict.attr] = reply.choice
            stats["resolved"] += 1
        queue = remaining
    stats["final_conflicts"] = len(queue)
    for conflict in queue:                       # still conflicting: drop the sentences, flag it
        for claim in conflict.claims:
            if claim.block and claim.block in texts:
                texts[claim.block], dropped = drop_sentences(texts[claim.block], conflict.values)
                stats["dropped"] += dropped
    return bible, texts, stats
