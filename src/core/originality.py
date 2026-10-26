# src/core/originality.py
"""Challenge task 6: everything except the organisation name and branding is fabricated.
* E-28: names from the source text (proper nouns) may not reappear in the bible, new entities or prose.
* E-27: category labels are replaced unless they are generic vocabulary (checked deterministically here).
* E-26: tone is measured from the source text, not assumed."""
import re

MONTHS = {m.lower() for m in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                              'September', 'October', 'November', 'December', 'Jan', 'Feb', 'Mar', 'Apr',
                              'Jun', 'Jul', 'Aug', 'Sep', 'Sept', 'Oct', 'Nov', 'Dec']}
CONTINENTS = {'africa', 'asia', 'europe', 'north america', 'south america', 'latin america', 'oceania',
              'australia', 'antarctica', 'middle east', 'asia pacific', 'apac', 'emea', 'americas',
              'rest of world', 'rest of the world', 'domestic', 'international', 'north', 'south', 'east', 'west',
              'central', 'others', 'other'}
LINE_ITEMS = {'revenue', 'revenues', 'total', 'subtotal', 'grand total', 'net income', 'net profit', 'gross profit',
              'operating profit', 'operating income', 'ebitda', 'ebit', 'cost of sales', 'cost of goods sold',
              'expenses', 'operating expenses', 'other income', 'tax', 'taxes', 'assets', 'liabilities', 'equity',
              'cash', 'debt', 'capex', 'headcount', 'employees', 'margin', 'units', 'volume', 'price', 'growth',
              'male', 'female', 'urban', 'rural', 'total population', 'households'}
GENERIC = MONTHS | CONTINENTS | LINE_ITEMS
PROPER_RE = re.compile(r"\b(?:[A-Z][\w&'\-]+)(?:\s+(?:(?:of|and|&|for|de|la)\s+)?[A-Z][\w&'\-]*)*\b")
STOP = {'The', 'A', 'An', 'In', 'On', 'Our', 'We', 'This', 'That', 'These', 'Those', 'It', 'For', 'With', 'As',
        'At', 'By', 'Of', 'And', 'To', 'From', 'During', 'Total', 'Note', 'Notes', 'Figure', 'Table', 'Chart'}


def is_generic_label(label: str) -> bool:
    t = re.sub(r'\s+', ' ', str(label).strip().lower())
    return t in GENERIC or bool(re.fullmatch(r'(q[1-4]|h[12]|fy)?\s?\d{2,4}', t))


def source_proper_nouns(text: str, keep: set[str]) -> set[str]:
    """Capitalised names (one or more words) from the source text, minus sentence-initial stop words and the
    organisation/brand terms in `keep` (which stay real)."""
    keep_l = {k.lower() for k in keep}
    out = set()
    for m in PROPER_RE.finditer(text):
        name = m.group(0).strip()
        words = name.split()
        while words and words[0] in STOP:
            words = words[1:]
        name = ' '.join(words)
        if len(name) < 3 or name.lower() in keep_l or name.lower() in GENERIC:
            continue
        if any(k in name.lower() for k in keep_l):
            continue
        out.add(name)
    return out


def reused_names(text_or_names, source_names: set[str]) -> list[str]:
    """Source names that reappear (case-insensitive, whole words) in generated text or a list of names."""
    hay = ' '.join(text_or_names) if isinstance(text_or_names, (list, set, tuple)) else text_or_names
    low = hay.lower()
    return sorted(n for n in source_names
                  if re.search(r'(?<![a-z])' + re.escape(n.lower()) + r'(?![a-z])', low))


def check_label_map(labels: list[tuple[str, str]]) -> list[str]:
    """(src, new) pairs -> errors: a non-generic label kept unchanged, or a duplicate new label."""
    errs, seen = [], set()
    for src, new in labels:
        if src.strip().lower() == new.strip().lower() and not is_generic_label(src):
            errs.append(f'label {src!r} is company-specific and must be replaced')
        if new.lower() in seen:
            errs.append(f'duplicate replacement {new!r}')
        seen.add(new.lower())
    return errs


def tone_profile(paragraphs: list[str]) -> dict:
    text = ' '.join(paragraphs)
    sents = [s for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    words = text.split()
    fp = sum(w.lower().strip('.,;:') in {'we', 'our', 'us'} for w in words)
    tp = sum(w.lower().strip('.,;:') in {'it', 'its', 'the company', 'they', 'their'} for w in words)
    person = 'first_plural' if fp > 2 * max(tp, 1) else ('third' if tp > 2 * max(fp, 1) else 'mixed')
    bullets = sum(p.lstrip().startswith(('•', '-', '–', '*')) for p in paragraphs)
    return {'avg_sentence_words': round(len(words) / max(len(sents), 1), 1), 'person': person,
            'contractions': bool(re.search(r"\b\w+'(s|re|ve|ll|d|t)\b", text)) and "n't" in text,
            'bullet_share': round(bullets / max(len(paragraphs), 1), 2)}


# ---------------------------------------------------------------- E-39: the guard on labels the LLM keeps
_WORD = re.compile(r"[A-Za-z][A-Za-z\-']*")


def name_words(text: str, organisation: str | None = None) -> set[str]:
    """Words that look like names of places, people or units: capitalised inside a prose sentence and
    never written in lowercase anywhere in the source; plus the organisation's own distinctive words.
    Abbreviations (all caps, or mixed case like 'QoQ') and generic vocabulary are not names."""
    lower = {w.lower() for w in _WORD.findall(text) if w[0].islower()}
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = _WORD.findall(sentence)
        if len(words) < 8 or sum(w[0].islower() for w in words) < 0.6 * len(words):
            continue                                   # not prose: a table row, a header, a list
        for w in words[1:]:
            if (len(w) >= 3 and w[0].isupper() and w[1:].islower() and w.lower() not in lower
                    and w.lower() not in GENERIC):
                out.add(w.lower())
    for w in _WORD.findall(organisation or ""):
        if len(w) >= 3 and w.lower() not in lower and w.lower() not in GENERIC:
            out.add(w.lower())
    return out


def names_in(label: str, names: set[str]) -> list[str]:
    """The name words a label contains (case-insensitive, whole words)."""
    return sorted({w.lower() for w in _WORD.findall(str(label))} & names)
