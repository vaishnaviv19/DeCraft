# src/narrative/_writer.py

"""Section Writer (M8.3): one versioned LLM call per section, returning a placeholder template.



The model receives a catalogue of fact KEYS with what each one means and, for words and labels, the

word itself, and for changes only their direction ("rose" / "fell") - never a number. Its draft is

rejected when it writes a digit outside a placeholder, uses a key that does not exist, or says a

figure rose when it fell (or the opposite); the caller then uses the deterministic template."""

from __future__ import annotations



import json

import re



from pydantic import BaseModel, ConfigDict



from src.config import Config

from src.llm import estimate_tokens
from src.llm import LLMGateway, PromptSpec

from src.narrative._placeholders import placeholder_keys, stray_numbers



SECTION_PROMPT = PromptSpec(
    name="section_template", version="v1", temperature=0.7, max_input_tokens=3200, max_output_tokens=1500,
    rationale="Pre-v3.2 section writer (renamed from section_writer so it never shares a name with the v3.2 writer); "
              "numbers only as placeholders, rose/fell checked by Python.",
    system='''You write ONE section of a document about the organisation in the data, as a template, in a natural, professional voice.

Return ONLY JSON: {"text": "..."} with no prose outside it. Separate paragraphs with a blank line.

Rules:

- Write numbers ONLY as placeholders {{ key }} (the table's own format is used) or {{ key|%:1 }};

  never write a digit yourself. Period names such as FY24 or Q3 may be written as words.

- Use only the fact keys supplied; "about" says what each one means. Never invent a key.

- Use the "moved" field: say a figure rose only if it rose, fell only if it fell. For a fall write

  e.g. "fell by {{ key|%:1|abs }}".

- Do not copy sentences from the source document.

- Follow the requested word count approximately.''')



MAX_DRAFT_CHARS, MAX_KEYS = 6000, 120

UP_WORDS = re.compile(r"\b(increas\w*|rose|rise[sn]?|rising|grew|grow(s|ing)?|higher|improv\w*|gain\w*|"

                      r"expand\w*|climb\w*|up)\b", re.I)

DOWN_WORDS = re.compile(r"\b(decreas\w*|declin\w*|fell|fall(s|ing|en)?|lower|drop\w*|contract\w*|"

                        r"shr[iau]nk\w*|down|weaken\w*)\b", re.I)

SENTENCE = re.compile(r"[^.!?]+[.!?]?")
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")





class SectionDraft(BaseModel):

    model_config = ConfigDict(extra="forbid")

    text: str





def _subject(key: str) -> str:

    rest = key.split(".", 1)[1] if "." in key else key

    return rest.rsplit(".", 1)[0].replace("row:", "")





def describe_fact(key: str, fact) -> str:

    what, suffix = _subject(key), key.rsplit(".", 1)[-1]

    return {"total": f"total of {what}", "average": f"average {what}", "largest": f"largest {what}",

            "largest_label": f"the item with the largest {what}", "largest_share": f"share of the largest {what} in its total",

            "growth": f"change in {what} from the first to the last period (percent)",

            "direction": f"overall direction of {what}", "latest": f"{what} in the latest period",

            "label": "name of a line item", "name": "name of a column", "first_period": "first period shown",

            "last_period": "last period shown", "first_label": "first item listed",

            "last_label": "last item listed"}.get(suffix, what)





def movement(key: str, facts: dict) -> str | None:

    """'rose' / 'fell' for a key whose series has a known change (growth sign, else direction word)."""

    base = key.rsplit(".", 1)[0]

    growth = facts.get(f"{base}.growth")

    if growth is not None and isinstance(growth.value, (int, float)) and growth.value:

        return "rose" if growth.value > 0 else "fell"

    direction = facts.get(f"{base}.direction")

    if direction is not None and direction.value in ("up", "down"):

        return "rose" if direction.value == "up" else "fell"

    return None





def catalogue(facts: dict, keys: list[str]) -> list[dict]:

    out = []

    for key in keys[:MAX_KEYS]:

        fact = facts[key]

        entry = {"key": key, "kind": fact.kind, "about": describe_fact(key, fact)}

        if fact.kind in ("text", "word"):

            entry["value"] = str(fact.value)

        if (moved := movement(key, facts)) is not None:

            entry["moved"] = moved

        out.append(entry)

    return out





def contradicts(template: str, facts: dict) -> bool:
    """True when a sentence says 'rose' about a figure that fell, or 'fell' about one that rose."""
    found = PLACEHOLDER_RE.findall(template)
    masked = PLACEHOLDER_RE.sub(lambda m: f" @@{found.index(m.group(0))}@@ ", template)
    for sentence in SENTENCE.findall(masked):               # keys contain dots: mask them first
        keys = [k for i in re.findall(r"@@(\d+)@@", sentence) for k in placeholder_keys(found[int(i)])]
        moves = {movement(k, facts) for k in keys} - {None}
        if len(moves) != 1:
            continue
        moved = moves.pop()
        words = re.sub(r"@@\d+@@", " ", sentence)
        if moved == "fell" and UP_WORDS.search(words) and not DOWN_WORDS.search(words):
            return True
        if moved == "rose" and DOWN_WORDS.search(words) and not UP_WORDS.search(words):
            return True
    return False


def build_prompt(section: dict, fact_keys: list[str], facts: dict | None = None,
                 errors: list[str] | None = None) -> str:

    payload = {"title": section.get("title"), "purpose": section.get("purpose"),

               "target_words": section.get("target_word_count"),

               "facts": catalogue(facts, fact_keys) if facts else [{"key": k} for k in fact_keys[:MAX_KEYS]],

               "example": "Revenue reached {{ orders.Revenue.total }} in the period, while costs "

                          "fell by {{ orders.Cost.growth|%:1|abs }}."}
    if errors:
        payload["errors"] = errors[:10]          # the previous draft's problems, to fix in this one

    return json.dumps(payload)





def section_first(section: dict, fact_keys: list[str]) -> list[str]:
    """Facts of the section's own tables first, then the document-level rest, order otherwise kept."""
    own = {str(t.get("table_id")) for t in section.get("tables") or [] if isinstance(t, dict)}
    mine = [k for k in fact_keys if k.split(".", 1)[0] in own]
    return mine + [k for k in fact_keys if k not in set(mine)]


def fitted_prompt(section: dict, fact_keys: list[str], facts: dict | None = None,
                  errors: list[str] | None = None) -> str:
    """The section payload cut to the prompt's input cap: the least relevant facts are dropped first.
    Before this, every section sent up to 120 facts, went over the cap and fell back to the template."""
    keys = section_first(section, fact_keys)[:MAX_KEYS]
    budget = SECTION_PROMPT.max_input_tokens - estimate_tokens(SECTION_PROMPT.system)
    while True:
        user = build_prompt(section, keys, facts, errors)
        if estimate_tokens(user) <= budget or len(keys) <= 1:
            return user
        keys = keys[:max(1, int(len(keys) * 0.8))]


def write_section(section: dict, fact_keys: list[str], cfg: Config, gateway: LLMGateway,
                  facts: dict | None = None) -> str | None:
    """A validated placeholder template, or None so the caller falls back to the deterministic text.
    A draft that fails a check is sent back with the list of errors (report.writer_retries times)."""
    errors: list[str] = []
    for _attempt in range(1 + max(0, cfg.report.writer_retries)):
        draft = gateway.ask_json(SECTION_PROMPT, fitted_prompt(section, fact_keys, facts, errors), SectionDraft)
        if draft is None:
            return None                              # no valid JSON after the gateway's own retries
        text = unescape_keys(draft.text)
        errors = draft_errors(text, fact_keys, facts)
        if not errors:
            return text
    return None


def unescape_keys(text: str) -> str:
    """Live run 2026-10-26: the model put a backslash before spaces inside placeholders ('EBITDA, backslash,
    space, Margin'). Dropping a backslash before a space or punctuation is safe: the key must still
    match a real fact exactly."""
    return PLACEHOLDER_RE.sub(lambda m: re.sub(r"\\([ ()%.,&/-])", r"\1", m.group(0)), text)


def draft_errors(text: str, fact_keys: list[str], facts: dict | None) -> list[str]:
    """Every reason a draft cannot be used; empty when it passes."""
    if not text.strip():
        return ["the text is empty"]
    if len(text) > MAX_DRAFT_CHARS:
        return [f"the text is longer than {MAX_DRAFT_CHARS} characters"]
    errors = []
    if stray := stray_numbers(text):
        errors.append(f"numbers written as digits instead of placeholders: {[str(x) for x in stray[:5]]}")
    known = set(fact_keys)
    if unknown := [k for k in placeholder_keys(text) if k not in known]:
        errors.append(f"placeholder keys that are not in 'facts' (use the keys exactly as given): {unknown[:5]}")
    if facts and contradicts(text, facts):
        errors.append("a sentence says a figure rose when it fell, or fell when it rose (see 'moved')")
    return errors


# ---------------------------------------------------------------- v3.2 block-level writing (M8.7)
from pydantic import Field  # noqa: E402


class WriterBlockOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str


class ClaimOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    e: str
    a: str = ""
    v: str = ""
    p: int | str = 0       # paragraph index; the model sometimes sends the sentence (fixed by locate_claims)
    b: str = ""


def locate_claims(reply) -> None:
    """Live run 2026-10-26: 4 of 21 writer replies put the sentence into 'p' instead of the paragraph
    index. Python finds that sentence in the claimed block and stores its paragraph index (0 if absent)."""
    texts = {block.id: block.text for block in reply.blocks}
    for claim in reply.claims:
        if isinstance(claim.p, int):
            continue
        if str(claim.p).strip().isdigit():
            claim.p = int(str(claim.p).strip())
            continue
        probe = str(claim.p).strip()[:40].lower()
        paragraphs = [p for p in texts.get(claim.b, "").split("\n\n")]
        claim.p = next((i for i, p in enumerate(paragraphs) if probe and probe in p.lower()), 0)


class NewEntityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str = "initiative"
    name: str = ""
    attrs: dict[str, str] = Field(default_factory=dict)


class WriterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[WriterBlockOut]
    claims: list[ClaimOut] = Field(default_factory=list)
    new: list[NewEntityOut] = Field(default_factory=list)


def _word_range(source_words: int) -> tuple[int, int]:
    """[min, max] words for a box. The MAX is the source box's own word count: that text fits the box at
    the locked font size (E-29), so a writer that stays at or under it never overflows (owner request
    2026-10-26: the model is told the number of words it must not exceed)."""
    words = max(1, int(source_words))
    return max(1, round(words * 0.85)), words


def _label_for(key: str) -> str:
    return key.split(".")[-1].replace("_", " ").strip().title()


MAX_AVOID = 60


def section_avoid(section, blocks: dict, avoid: list[str] | set[str] | None) -> list[str]:
    """The 60 avoid-list names most related to the section: names in its own source blocks first."""
    names = sorted(avoid or [])
    own = " ".join((blocks[b].text or "") for b in section.block_ids if b in blocks).lower()
    near = [n for n in names if n.lower() in own]
    return (near + [n for n in names if n not in near])[:MAX_AVOID]


MAX_RETRY_ERRORS, MAX_ERROR_CHARS = 8, 200


def retry_payload(section, outline, bible, facts: dict, periods: list[str], cap: int, blocks: dict,
                  errors: list[str], **kwargs) -> str:
    """The retry message (payload + the errors to fix) inside the same input cap: the errors are
    shortened and the payload is rebuilt with less room, so optional fields go first. Live run
    2026-10-26: the retry was 3,666 tokens against a 3,200 cap and stopped the stage."""
    from src.llm import estimate_tokens
    err = [str(e)[:MAX_ERROR_CHARS] for e in errors[:MAX_RETRY_ERRORS]]
    room = cap - estimate_tokens(json.dumps({"payload": {}, "err": err})) - 20
    try:
        payload, _ = build_writer_payload(section, outline, bible, facts, periods, max(room, 1), blocks, **kwargs)
    except ValueError:                         # even the required part no longer fits next to the errors
        payload, _ = build_writer_payload(section, outline, bible, facts, periods, cap, blocks, **kwargs)
        err = err[:2]
    return json.dumps({"payload": json.loads(payload), "err": err})


def build_writer_payload(section, outline, bible, facts: dict, periods: list[str], cap: int,
                         blocks: dict, tone: dict | None = None, avoid=None, dt: str = "",
                         dom: str = "", rep: list[str] | None = None) -> tuple[str, list[str]]:
    """Writer payload (E-12/E-05): required dt/dom/tone/sid/t/g/bl/f/per/avoid (spec M8.7.1; E-26 tone,
    E-28 avoid); optional bi, ol, else in that priority."""
    from src.llm import fit_payload

    block_items = []
    for block_id in section.block_ids:
        block = blocks.get(block_id)
        if block is None:
            continue
        low, high = _word_range(block.source_words)
        block_items.append({"id": block_id, "k": block.kind, "w": [low, high]})
    fact_items = [{"k": key, "l": _label_for(key), "u": facts[key].unit, "band": facts[key].band}
                  for key in section.fact_keys if key in facts]
    required = {"dt": dt, "dom": dom, "tone": tone or {}, "sid": section.section_id, "t": section.title,
                "g": section.goal, "bl": block_items, "f": fact_items, "per": list(periods),
                "avoid": section_avoid(section, blocks, avoid)}
    if rep:                                  # v3.3 F-11: sentences other sections already say
        required["rep"] = list(rep)[:6]
    entities = [{"id": entity.id, "kind": entity.kind, "name": entity.name, "attrs": entity.attrs}
                for entity in bible.entities]
    others = [other for other in outline.sections if other.section_id != section.section_id]
    optional = [
        ("bi", entities),
        ("ol", [{"sid": other.section_id, "t": other.title, "g": other.goal} for other in others]),
        ("else", [key for other in others for key in other.fact_keys][:10]),
    ]
    return fit_payload(required, optional, cap)


def _longest_run(text: str, source: str) -> int:
    def words(value: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", value.lower())

    target, origin = words(text), words(source)
    if not target or not origin:
        return 0
    best = 0
    for i in range(len(target)):
        for j in range(len(origin)):
            size = 0
            while i + size < len(target) and j + size < len(origin) \
                    and target[i + size] == origin[j + size]:
                size += 1
            best = max(best, size)
    return best


def check_section(reply: WriterOut, section, facts: dict, bible, periods: list[str],
                  source_text: str, blocks: dict, cfg: Config, avoid=None) -> list[str]:
    """Every check from M8.7.3; an empty list means the reply may be used."""
    from src.core.originality import reused_names
    from src.narrative._tokens import illegal_digits

    errors: list[str] = []
    names = set(avoid or [])
    if names:                                  # E-28: no source name in the text or in new entities
        if hit := reused_names(" ".join(block.text for block in reply.blocks), names):
            errors.append(f"uses names from 'avoid': {hit[:5]}")
        if hit := reused_names([entity.name for entity in reply.new], names):
            errors.append(f"new entities reuse names from 'avoid': {hit[:5]}")
    expected = [block_id for block_id in section.block_ids if block_id in blocks]
    got = [block.id for block in reply.blocks]
    if expected and sorted(got) != sorted(expected):
        missing = sorted(set(expected) - set(got))
        extra = sorted(set(got) - set(expected))
        if missing:
            errors.append(f"missing block ids: {missing[:5]}")
        if extra:
            errors.append(f"unknown block ids: {extra[:5]}")
    allowed = set(periods)
    for block in reply.blocks:
        low, high = _word_range(blocks[block.id].source_words) if block.id in blocks else (1, 10 ** 6)
        words = len(block.text.split())
        if words > high:                                  # a hard limit: the box holds no more
            errors.append(f"{block.id}: {words} words, more than the maximum {high}; shorten it to at most "
                          f"{high} words")
        elif words < low * 0.8:
            errors.append(f"{block.id}: {words} words, fewer than the minimum {low}")
        bad = illegal_digits(block.text, allowed)
        if bad:
            errors.append(f"{block.id}: digits not allowed outside placeholders/period tokens: {bad[:5]}")
        for key in placeholder_keys(block.text):
            if key not in facts:
                errors.append(f"{block.id}: unknown placeholder key {key!r}")
        if block.text.count("{{") != block.text.count("}}"):
            errors.append(f"{block.id}: unclosed placeholder")
        from src.narrative._autounits import unit_word_violations
        typed = unit_word_violations(block.text)          # v3.3 F-03: |auto writes scale and currency
        if typed:
            errors.append(f"{block.id}: do not type unit, scale or currency words next to a placeholder; "
                          f"write the placeholder as key|auto instead: {typed[:5]}")
    known_entities = {entity.id for entity in bible.entities} | {entity.id for entity in reply.new}
    for claim in reply.claims:
        if claim.e not in known_entities:
            errors.append(f"claim references an unknown entity {claim.e!r}")
    limit = cfg.thresholds.ngram_overlap_max
    if source_text and _longest_run(" ".join(block.text for block in reply.blocks),
                                    source_text) >= limit:
        errors.append(f"copies {limit}+ consecutive words from the source text")
    return errors


def _fallback_section(section, facts: dict, max_words: int = 180, index: int = 0) -> str:
    """Template text from the SECTION's own tables. Before, an empty table list made every section
    describe the whole document with the same sentences."""
    from src.narrative._placeholders import resolve
    from src.narrative._templates import section_template
    tables = list(dict.fromkeys(key.split(".", 1)[0] for key in section.fact_keys))
    own = {key: fact for key, fact in facts.items() if key.split(".", 1)[0] in tables} or facts
    if not tables:
        # no figures of its own: general sentences, varied per section, up to the section's word budget
        # (one repeated sentence per section left an LLM-off run at 489 words, below the 1,000 minimum)
        from src.narrative._templates import COMMENTARY
        sentences, words, k = [], 0, 0
        # Cycle the pool until the section's body budget is met. Stopping at one pass left long
        # sections (e.g. a disclaimer page) with too little text, so later boxes stayed empty and the
        # per-page word gate failed (M8.9/M8.12).
        while words < max_words:
            sentence = COMMENTARY[(index * 7 + k) % len(COMMENTARY)]
            sentences.append(sentence)
            words += len(sentence.split())
            k += 1
            if k > 10 * len(COMMENTARY):
                break
        return "\n\n".join(" ".join(sentences[i:i + 3]) for i in range(0, len(sentences), 3))
    try:
        text = resolve(section_template({"title": section.title,
                                         "tables": [{"table_id": t} for t in tables],
                                         "target_word_count": max_words}, own, index), own)
    except Exception:
        return f"This section covers {section.title or section.section_id}."
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
        if "." in text:
            text = text[: text.rfind(".") + 1]
    return text


BODY_KINDS = {"text", "caption"}       # headings show the section titles; furniture keeps its own text

# A tiny source box (a 3-7 word caption or label-sized Text block) cannot take any whole sentence from the
# fallback narrative. Leaving it empty makes the page look unfinished and lowers its word count, so use a
# short generic sentence that fits instead; the skipped sentence stays available for the next box.
SHORT_FILLER = {
    "caption": ("The figure is shown below in detail.", "The figures are shown below.",
                "The figure is shown below.", "The chart appears below.", "The visual is illustrative."),
    "text": ("The result was broadly stable.", "The figures are shown below in detail.",
             "The figures are shown below.", "The period was mixed.", "The trend continued.",
             "Details follow."),
}


def _short_filler(kind: str, budget: float, block_id: str) -> str:
    pool = SHORT_FILLER.get(kind, SHORT_FILLER["text"])
    fit = [phrase for phrase in pool if len(phrase.split()) <= budget * 1.15]
    if not fit:
        return pool[-1]
    start = sum(ord(character) for character in block_id) % len(fit)
    return fit[start]



def _split_for_blocks(section, text: str, blocks: dict) -> dict[str, str]:
    """Spread deterministic text over the section's body boxes (text, caption) by their source word
    budgets, whole sentences only and without overshooting a box by more than 15% (E-13). Before, a
    5-word box took a 15-word sentence, later boxes got nothing, and heading boxes took body text
    that was then replaced by the section title."""
    ids = [block_id for block_id in section.block_ids
           if block_id in blocks and blocks[block_id].kind in BODY_KINDS]
    if not ids:
        return {}
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    out: dict[str, str] = {}
    index = 0
    for position, block_id in enumerate(ids):
        budget = max(1, int(blocks[block_id].source_words or 1))
        chunk: list[str] = []
        words = 0
        while index < len(sentences):
            size = len(sentences[index].split())
            last = position == len(ids) - 1
            if chunk and words + size > budget * 1.15 and not last:
                break
            if not chunk and size > budget * 1.15 and not last:
                break                                   # too small for any sentence: stays empty
            chunk.append(sentences[index])
            words += size
            index += 1
            if words >= budget and not last:
                break
        out[block_id] = " ".join(chunk).strip()
    return out


def write_blocks(data, outline, bible, templates, style, cfg: Config,
                 gateway: LLMGateway | None, avoid=None, dt: str = "", dom: str = ""):
    """Parallel block-level writers (E-12): validate, retry with the errors, then fall back.
    Every payload carries the source tone (E-26) and the avoid list (E-28); `avoid` defaults to the
    proper nouns of the source text, minus the organisation's name."""
    from concurrent.futures import ThreadPoolExecutor

    from src.contracts import NarrativeDoc, NarrativeSection
    import pandas as pd

    from src.core.originality import drop_shown, source_proper_nouns, tone_profile
    from src.llm import estimate_tokens
    from src.narrative._placeholders import resolve
    from src.narrative._prompts import WRITER

    blocks = {block.block_id: block for page in templates for block in page.blocks}
    periods = list(getattr(data, "allowed_periods", []))
    cap = max(200, WRITER.max_input_tokens - estimate_tokens(WRITER.system))
    source_text = " ".join(block.text or "" for block in blocks.values() if block.text)
    tone = tone_profile([block.text for block in blocks.values() if block.kind == "text" and block.text])
    from src.narrative._autounits import detect_scale_style
    scale_style = detect_scale_style(source_text)           # v3.3 F-03: once per document
    if avoid is None:
        avoid = source_proper_nouns(source_text, keep={outline.report_title} if outline.report_title else set())
    shown = [str(v) for frame in list(data.display.values()) + list(data.tables.values())
             for column in frame.columns
             if not (pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_datetime64_any_dtype(frame[column]))
             for v in frame[column].dropna().unique()]
    avoid = drop_shown(avoid, shown + [str(c) for frame in data.display.values() for c in frame.columns])
    order = {section.section_id: i for i, section in enumerate(outline.sections)}

    def one(section, rep=None):
        from src.narrative._claims import Claim, Entity
        from src.narrative._consistency import rename_new

        # the section's BODY budget (text and caption boxes; headings show the title instead)
        limit = max(60, sum(blocks[block_id].source_words for block_id in section.block_ids
                            if block_id in blocks and blocks[block_id].kind in BODY_KINDS) or 120)
        index = order.get(section.section_id, 0)       # varies the template sentences per section

        def fallback():
            return _fallback_section(section, data.facts, limit, index)

        def fallback_result(attempts, dropped_list, cap=False):
            text = fallback()
            return (section, text, attempts, False,
                    ["payload exceeds cap"] if cap else dropped_list,
                    _split_for_blocks(section, text, blocks), [], [])

        try:
            payload, dropped = build_writer_payload(section, outline, bible, data.facts, periods,
                                                    cap, blocks, tone=tone, avoid=avoid, dt=dt, dom=dom,
                                                    rep=rep)
        except ValueError:                     # required payload over the cap: the caller splits it
            return fallback_result(0, [], cap=True)
        if gateway is None or not gateway.enabled:
            return fallback_result(0, dropped)
        from src.errors import LLMBudgetExceeded

        errors: list[str] = []
        for attempt in range(1, 1 + max(0, cfg.report.writer_retries) + 1):
            user = payload if not errors else retry_payload(
                section, outline, bible, data.facts, periods, cap, blocks, errors,
                tone=tone, avoid=avoid, dt=dt, dom=dom, rep=rep)
            try:
                reply = gateway.ask_json(WRITER, user, WriterOut)
            except LLMBudgetExceeded:              # over the cap or the stage budget: this section only
                return fallback_result(attempt, dropped)
            if reply is None:
                return fallback_result(attempt, dropped)
            for block in reply.blocks:                 # 'EBITDA\ Margin' inside placeholders (live run)
                block.text = unescape_keys(block.text)
            locate_claims(reply)
            errors = check_section(reply, section, data.facts, bible, periods, source_text,
                                   blocks, cfg, avoid=avoid)
            if not errors:
                block_texts = {block.id: resolve(block.text, data.facts, scale_style)
                               for block in reply.blocks}
                text = "\n\n".join(block_texts.values())
                claims = [Claim(section=section.section_id, paragraph=claim.p, entity=claim.e,
                                attr=claim.a, value=claim.v, block=claim.b)
                          for claim in reply.claims]
                new_entities = rename_new(section.section_id, [
                    Entity(id=item.id, kind=item.kind, name=item.name, attrs=dict(item.attrs))
                    for item in reply.new])
                return section, text, attempt, True, dropped, block_texts, claims, new_entities
        return fallback_result(cfg.report.writer_retries + 1, dropped)

    concurrency = max(1, cfg.providers.text.max_concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, outline.sections))
    sections, flags, used = [], [], False
    all_claims, all_new, texts = [], [], {}
    for section, text, attempts, ok, dropped, block_texts, claims, new_entities in results:
        sections.append(NarrativeSection(section_id=section.section_id, title=section.title,
                                         text=text))
        texts.update(block_texts)
        all_claims.extend(claims)
        all_new.extend(new_entities)
        used = used or ok
        llm_on = gateway is not None and gateway.enabled
        if not ok and llm_on:
            flags.append(f"{section.section_id}: fallback after {attempts} attempt(s)")
        if llm_on:
            for name in dropped:
                flags.append(f"{section.section_id}: dropped optional field {name}")

    stats = {"claims": len(all_claims), "new_entities": len(all_new), "conflicts": 0,
             "resolved": 0, "dropped": 0, "unknown_refs": 0}
    if all_claims or all_new:
        from src.narrative._consistency import (_capitalised_names, drop_sentences, merge,
                                                reconcile)
        entities, conflicts, unknown = merge(bible, all_claims, all_new)
        stats["unknown_refs"] = len(unknown)
        for claim in unknown:                       # a failed claim: drop its sentence (E-08)
            if claim.block and claim.block in texts:
                texts[claim.block], count = drop_sentences(texts[claim.block], (claim.value,))
                stats["dropped"] += count
        stats["conflicts"] = len(conflicts)
        if conflicts:
            allowed = {entity.name for entity in bible.entities} | _capitalised_names(source_text)
            entities, texts, outcome = reconcile(entities, conflicts, texts, cfg, gateway, periods,
                                                 data.facts, allowed)
            stats["conflicts"] = outcome.get("conflicts_initial", stats["conflicts"])
            stats["resolved"] = outcome.get("resolved", 0)
            stats["dropped"] += outcome.get("dropped", 0)
            flags.append(f"conflicts: {stats['conflicts']} initial, {stats['resolved']} resolved, "
                         f"{stats['dropped']} sentences dropped")
        for index, section in enumerate(sections):
            ids = [block_id for block_id in outline.sections[index].block_ids if block_id in texts]
            if ids:
                sections[index].text = "\n\n".join(texts[block_id] for block_id in ids)

    # v3.3 F-11: sentences repeated across sections written in parallel. The later section is rewritten
    # once with 'rep'; a repeat that survives is dropped when its box keeps its minimum length, else flagged.
    repeats = _resolve_repeats(sections, outline, texts, blocks, cfg, gateway, one)
    if repeats["found"]:
        flags.append(f"repeats: {repeats['found']} found, {repeats['fixed']} fixed, "
                     f"{repeats['dropped']} dropped, {repeats['flagged']} flagged")
    doc = NarrativeDoc(sections=sections,
                       word_count=sum(len(section.text.split()) for section in sections),
                       llm_used=used, claims=stats["claims"],
                       new_entities=stats["new_entities"], conflicts=stats["conflicts"],
                       resolved=stats["resolved"], dropped=stats["dropped"],
                       unknown_refs=stats["unknown_refs"])
    doc.blocks = texts
    doc.flags = flags
    doc.repeats = repeats
    return doc


def _section_texts(outline, texts: dict) -> dict[str, str]:
    return {section.section_id: " ".join(texts.get(block_id, "") for block_id in section.block_ids)
            for section in outline.sections}


def _resolve_repeats(sections, outline, texts: dict, blocks: dict, cfg, gateway, one) -> dict:
    from src.narrative._repeats import cross_section_repeats, drop_sentence
    order = {section.section_id: i for i, section in enumerate(outline.sections)}
    threshold = cfg.narrative.repeat_threshold
    hits = cross_section_repeats(_section_texts(outline, texts), threshold)
    stats = {"found": len(hits), "fixed": 0, "dropped": 0, "flagged": 0, "flagged_sentences": []}
    if not hits:
        return stats
    later: dict[str, list[str]] = {}
    for a, sentence_a, b, sentence_b, _ in hits:
        sid, keep = (b, sentence_a) if order.get(b, 0) > order.get(a, 0) else (a, sentence_b)
        later.setdefault(sid, []).append(keep)
    by_id = {section.section_id: section for section in outline.sections}
    if gateway is not None and gateway.enabled:
        for sid, avoid_sentences in later.items():
            result = one(by_id[sid], rep=avoid_sentences)      # one retry, the repeats listed in 'rep'
            if result[3]:                                       # ok
                texts.update(result[5])
    remaining = cross_section_repeats(_section_texts(outline, texts), threshold)
    stats["fixed"] = max(0, len(hits) - len(remaining))
    for a, sentence_a, b, sentence_b, _ in remaining:
        sid, sentence = (b, sentence_b) if order.get(b, 0) > order.get(a, 0) else (a, sentence_a)
        block_id = next((block_id for block_id in by_id[sid].block_ids
                         if sentence in texts.get(block_id, "")), None)
        if block_id is None:
            continue
        shorter = drop_sentence(texts[block_id], sentence)
        low, _ = _word_range(blocks[block_id].source_words) if block_id in blocks else (1, 0)
        if len(shorter.split()) >= low * 0.8:
            texts[block_id] = shorter
            stats["dropped"] += 1
        else:
            stats["flagged"] += 1
            stats["flagged_sentences"].append(sentence[:120])
    for index, section in enumerate(sections):
        ids = [block_id for block_id in outline.sections[index].block_ids if block_id in texts]
        if ids:
            sections[index].text = "\n\n".join(texts[block_id] for block_id in ids)
    return stats
