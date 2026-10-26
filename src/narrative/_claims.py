# src/narrative/_claims.py
"""Story Bible + claims merge (decisions E-08, E-09). Agents never talk to each other: writers read a frozen
StoryBible and return Claims; Python finds conflicts; only conflicts go to the Reconciler."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str                      # 'E3' (planner) or 'N:<section>:<k>' (new, from a writer)
    kind: str                    # 'product' | 'event' | 'person_role' | 'initiative' | 'place' | 'org_unit'
    name: str
    attrs: dict[str, str] = field(default_factory=dict)
    introduced_in: str | None = None


@dataclass(frozen=True)
class Claim:
    section: str
    paragraph: int
    entity: str                  # bible id, or the temporary id of a NEW entity
    attr: str                    # 'name' | 'year' | 'location' | 'role' | ...
    value: str


@dataclass(frozen=True)
class Conflict:
    entity: str
    attr: str
    values: tuple[str, ...]      # all competing values (bible value first when present)
    claims: tuple[Claim, ...]


def norm(v: str) -> str:
    return " ".join(str(v).lower().replace("fy ", "fy").split())


def merge_claims(bible: dict[str, Entity], claims: list[Claim], new_entities: list[Entity]):
    """-> (updated bible copy, conflicts, unknown_refs).
    * claim on a bible entity with a different value for an existing attr -> conflict
    * claim on a bible entity for a NEW attr -> attr added (first writer wins only if no other writer disagrees)
    * two writers giving different values for the same new attr -> conflict
    * new entity whose name equals an existing entity's name -> merged into it (same thing, different id)
    * claim referring to an id that is neither in the bible nor among new entities -> unknown_ref"""
    out = {k: Entity(e.id, e.kind, e.name, dict(e.attrs), e.introduced_in) for k, e in bible.items()}
    alias: dict[str, str] = {}
    by_name = {norm(e.name): e.id for e in out.values()}
    for ne in new_entities:
        if norm(ne.name) in by_name:
            alias[ne.id] = by_name[norm(ne.name)]
        else:
            out[ne.id] = Entity(ne.id, ne.kind, ne.name, dict(ne.attrs), ne.introduced_in)
            by_name[norm(ne.name)] = ne.id
    groups: dict[tuple[str, str], list[Claim]] = {}
    unknown = []
    for c in claims:
        eid = alias.get(c.entity, c.entity)
        if eid not in out:
            unknown.append(c)
            continue
        groups.setdefault((eid, c.attr), []).append(c)
    conflicts = []
    for (eid, attr), cs in sorted(groups.items()):
        ent = out[eid]
        current = ent.name if attr == "name" else ent.attrs.get(attr)
        values = {norm(c.value) for c in cs}
        if current is not None:
            if values - {norm(current)}:
                ordered = (current, *sorted({c.value for c in cs if norm(c.value) != norm(current)}))
                conflicts.append(Conflict(eid, attr, ordered, tuple(cs)))
        elif len(values) > 1:
            conflicts.append(Conflict(eid, attr, tuple(sorted({c.value for c in cs})), tuple(cs)))
        else:
            ent.attrs[attr] = cs[0].value
    return out, conflicts, unknown


def check_resolution(conflict: Conflict, chosen: str) -> bool:
    """Anti-hallucination guard 'choose, don't invent': the Reconciler's value must be one of the candidates."""
    return norm(chosen) in {norm(v) for v in conflict.values}
