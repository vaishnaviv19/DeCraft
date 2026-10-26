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


def build_prompt(section: dict, fact_keys: list[str], facts: dict | None = None) -> str:

    payload = {"title": section.get("title"), "purpose": section.get("purpose"),

               "target_words": section.get("target_word_count"),

               "facts": catalogue(facts, fact_keys) if facts else [{"key": k} for k in fact_keys[:MAX_KEYS]],

               "example": "Revenue reached {{ orders.Revenue.total }} in the period, while costs "

                          "fell by {{ orders.Cost.growth|%:1|abs }}."}

    return json.dumps(payload)





def section_first(section: dict, fact_keys: list[str]) -> list[str]:
    """Facts of the section's own tables first, then the document-level rest, order otherwise kept."""
    own = {str(t.get("table_id")) for t in section.get("tables") or [] if isinstance(t, dict)}
    mine = [k for k in fact_keys if k.split(".", 1)[0] in own]
    return mine + [k for k in fact_keys if k not in set(mine)]


def fitted_prompt(section: dict, fact_keys: list[str], facts: dict | None = None) -> str:
    """The section payload cut to the prompt's input cap: the least relevant facts are dropped first.
    Before this, every section sent up to 120 facts, went over the cap and fell back to the template."""
    keys = section_first(section, fact_keys)[:MAX_KEYS]
    budget = SECTION_PROMPT.max_input_tokens - estimate_tokens(SECTION_PROMPT.system)
    while True:
        user = build_prompt(section, keys, facts)
        if estimate_tokens(user) <= budget or len(keys) <= 1:
            return user
        keys = keys[:max(1, int(len(keys) * 0.8))]


def write_section(section: dict, fact_keys: list[str], cfg: Config, gateway: LLMGateway,

                  facts: dict | None = None) -> str | None:

    """A validated placeholder template, or None so the caller falls back to the deterministic text."""

    draft = gateway.ask_json(SECTION_PROMPT, fitted_prompt(section, fact_keys, facts), SectionDraft)

    if draft is None or not draft.text.strip() or len(draft.text) > MAX_DRAFT_CHARS:

        return None

    if stray_numbers(draft.text):

        return None

    if any(key not in fact_keys for key in placeholder_keys(draft.text)):

        return None

    if facts and contradicts(draft.text, facts):

        return None

    return draft.text

