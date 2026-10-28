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
              'male', 'female', 'urban', 'rural', 'total population', 'households',
              # E-27 model-off path: standard line items of a financial statement. The LLM keeps these;
              # without one, the deterministic list must, or the income statement becomes 'Item D' ...
              'depreciation', 'amortisation', 'amortization', 'depreciation and amortisation',
              'depreciation and amortization', 'interest', 'interest expense', 'interest income',
              'finance costs', 'finance cost', 'borrowing costs', 'other expenses', 'other operating expenses',
              'employee benefits expense', 'employee benefit expense', 'employee costs', 'employee cost',
              'cost of materials consumed', 'cost of raw materials', 'purchases', 'changes in inventories',
              'power and fuel', 'freight and forwarding', 'repairs and maintenance', 'manufacturing expenses',
              'total income', 'total expenses', 'total revenue', 'revenue from operations', 'income from operations',
              'pbt', 'profit before tax', 'profit before exceptional items', 'pat', 'profit after tax',
              'reported pat', 'profit attributable to shareholders', 'minority interest',
              'non-controlling interest', 'share of profit from associate', 'share of profit from associates',
              'share of associates', 'adjustments', 'exceptional items', 'exceptional income',
              'exceptional expense', 'tax expense', 'current tax', 'deferred tax', 'impairment', 'provisions',
              'ebitda margin', 'ebitda margins', 'ebit margin', 'ebit margins', 'gross margin', 'operating margin',
              'net margin', 'contribution margin', 'eps', 'earnings per share', 'basic eps', 'diluted eps',
              'dividend', 'dividend per share', 'dps', 'net worth', 'net debt', 'book value', 'capital employed',
              'working capital', 'return on equity', 'roe', 'return on capital employed', 'roce',
              'return on assets', 'roa', 'current ratio', 'debt to equity', 'debt-equity ratio',
              'interest coverage', 'interest coverage ratio', 'trade receivables', 'trade payables',
              'receivables', 'payables', 'inventory', 'inventories', 'borrowings', 'fixed assets',
              'property plant and equipment', 'intangible assets', 'goodwill', 'operating cash flow',
              'cash flow from operations', 'free cash flow', 'net cash flow', 'other comprehensive income',
              'oci', 'comprehensive income', 'segment revenue', 'segment result', 'segment ebitda',
              'installed capacity', 'capacity utilisation', 'capacity utilization', 'plant load factor', 'plf',
              'units sold', 'sales volume', 'volumes', 'realisation', 'realization', 'average realisation',
              'market share', 'order book', 'customer base', 'attrition', 'attrition rate', 'generation',
              'transmission', 'distribution', 'trading', 'retail', 'wholesale', 'yoy', 'qoq',
              'yoy growth', 'qoq growth', 'per unit', 'per employee',
              'adj pat', 'adj. pat', 'adj eps', 'adj. eps', 'adjusted pat', 'adjusted eps',
              'consolidated', 'standalone', 'company data', 'year', 'years', 'period', 'periods',
              'quarter', 'quarters', 'month', 'months', 'date', 'dates', 'particulars', 'description',
              'item', 'items', 'category', 'categories', 'segment', 'segments', 'business', 'businesses',
              'product', 'products', 'region', 'regions', 'country', 'countries', 'metric', 'metrics',
              'parameter', 'parameters', 'note', 'notes', 'year / rs. cr', 'year / rs cr',
              'margins', 'market cap', 'market capitalisation', 'market capitalization',
              'enterprise value', 'outstanding shares', 'free float', 'share price', 'stock price',
              'face value', 'dividend yield', 'price to earnings', 'p/e', 'ev/ebitda', 'net sales',
              'net revenue'}

# Market-data rows and period stubs. They are the standard vocabulary of a financial-facts table,
# not names of the organisation (a similar list to the model's own "keep" instruction in E-35).
MARKET_METRICS = {'52 week high', '52 week low', '52-week high', '52-week low', 'week high', 'week low',
                  'average volume', '6m average volume', 'volume', 'beta', 'shareholding',
                  'shareholding %', 'shareholding pattern', 'promoters', 'promoter', 'fii', 'fiis',
                  "fii's", 'mf', 'mfs', 'insti', 'institutions', 'public', 'pledge', 'promoters pledge',
                  "promoters' pledge", 'price performance', 'absolute return', 'relative return',
                  'absolute sensex', 'sensex', 'nifty', 'index', 'benchmark', 'return', 'returns',
                  'no. of shares', 'number of shares', 'shares', '1 week', '1 month', '3 month',
                  '3 months', '6 month', '6 months', '1 year', '12 month', 'ytd', 'high', 'low',
                  'mfs insti', 'mf insti', 'fii insti'}
GENERIC = MONTHS | CONTINENTS | LINE_ITEMS | MARKET_METRICS

# Qualifiers that may follow a generic line item without turning it into a name ("EBITDA margins",
# "PAT att. to shareholders", "Revenue growth %").
QUALIFIERS = {'margin', 'margins', 'growth', 'growth %', 'rate', 'rates', 'ratio', 'ratios', '%', 'bps',
              '(bps)', '(%)', 'per share', 'before tax', 'after tax', 'before exceptional items',
              'after exceptional items', 'att. to shareholders', 'attributable to shareholders',
              'from associate', 'from associates', 'net', 'gross', 'reported', 'adjusted', 'total', 'average',
              'per unit', 'per employee', 'per annum', 'year on year', 'quarter on quarter', 'yoy', 'qoq',
              'pat', 'eps', 'rs cr', 'rs. cr', 'cr', 'rs', 'inr cr', 'usd mn', 'value', 'values',
              'amount', 'amounts'}
UNIT_TAIL_RE = re.compile(r'\s*(\((?:%|bps|x|cr|mn|bn|rs\.?|inr|usd|\$)\)|%)\s*$', re.I)
PROPER_RE = re.compile(r"\b(?:[A-Z][\w&'\-]+)(?:\s+(?:(?:of|and|&|for|de|la)\s+)?[A-Z][\w&'\-]*)*\b")
STOP = {'The', 'A', 'An', 'In', 'On', 'Our', 'We', 'This', 'That', 'These', 'Those', 'It', 'For', 'With', 'As',
        'At', 'By', 'Of', 'And', 'To', 'From', 'During', 'Total', 'Note', 'Notes', 'Figure', 'Table', 'Chart'}


def is_generic_label(label: str) -> bool:
    t = re.sub(r'\s+', ' ', str(label).strip().strip(' -').lower()).replace('/', ' ')
    t = re.sub(r'\s+', ' ', t).strip()
    if t in GENERIC or bool(re.fullmatch(r'(q[1-4]|h[12]|fy)?\s?\d{2,4}[eap]?', t)):
        return True
    if re.fullmatch(r'\d{1,2}\s?(day|days|week|weeks|month|months|year|years|yr|yrs|m|w|d)', t):
        return True                                  # a column stub such as '3 Month' or '1 Year'
    core = UNIT_TAIL_RE.sub('', t).strip()
    if core in GENERIC:
        return True
    words = core.split()
    for n in (3, 2, 1):
        head = ' '.join(words[:n])
        rest = ' '.join(words[n:])
        if head in GENERIC and (not rest or rest in QUALIFIERS):
            return True
    return False


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
        # a table row read across ('- Industrial Energy P/BV 2.0x') ends in a one-letter fragment of
        # the next cell; the name is the words before it
        while len(words) > 1 and len(words[-1].strip("'-&")) <= 1:
            words = words[:-1]
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


def drop_shown(avoid, shown_labels) -> set[str]:
    """Avoid-list names that the output's own tables already show (kept standard terms such as
    'Depreciation', or fabricated labels) may be written about; every other source name stays banned.
    Live run 2026-10-26: 90 of 261 avoid names were kept table vocabulary."""
    hay = [" " + re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip() + " " for label in shown_labels if label]
    out = set()
    for name in avoid:
        needle = " " + re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip() + " "
        if needle.strip() and not any(needle in h for h in hay):
            out.add(name)
    return out


# ---------------------------------------------------------------- E-52: model-off label decisions
ENGLISH_SUFFIXES = ('tion', 'tions', 'sion', 'sions', 'ment', 'ments', 'ness', 'ity', 'ities', 'ance', 'ances',
                    'ence', 'ences', 'able', 'ables', 'ible', 'ive', 'ives', 'ory', 'ories', 'ary', 'aries',
                    'ings', 'ing', 'ed', 'al', 'als', 'ial', 'ure', 'ures', 'ers', 'ors', 'ics', 'ism', 'ist',
                    'ists', 'age', 'ages', 'ship', 'ous', 'ful', 'less', 'ant', 'ants', 'ent', 'ents', 'ise',
                    'ize', 'ised', 'ized', 'ly', 'est')
GENERIC_WORDS = {w for phrase in GENERIC | QUALIFIERS for w in re.findall(r"[a-z]+", phrase)}


def _stems(word: str) -> set[str]:
    w = word.lower()
    out = {w}
    if w.endswith("ies") and len(w) > 4:
        out.add(w[:-3] + "y")
    if w.endswith("es") and len(w) > 4:
        out.add(w[:-2])
    if w.endswith("s") and len(w) > 3:
        out.add(w[:-1])
    if w.endswith("'s"):
        out.add(w[:-2])
    return out


_PLACES: set[str] | None = None


def known_places() -> set[str]:
    """Lower-case city / state / country names (Faker's address data for a few English locales)."""
    global _PLACES
    if _PLACES is None:
        places: set[str] = set()
        try:
            import importlib
            for locale in ("en_IN", "en_US", "en_GB", "en_AU", "en_CA"):
                provider = importlib.import_module(f"faker.providers.address.{locale}").Provider
                for attr in ("cities", "city_names", "states", "countries"):
                    for value in getattr(provider, attr, ()) or ():
                        for w in re.findall(r"[A-Za-z]{3,}", str(value)):
                            places.add(w.lower())
        except Exception:                       # Faker missing: prose evidence still works
            pass
        _PLACES = {w for w in places if w not in GENERIC_WORDS
                   and not any(w.endswith(x) for x in ENGLISH_SUFFIXES)}
    return _PLACES


_COMMON: set[str] | None = None


def common_words() -> set[str]:
    """Ordinary English words (Faker's lorem, company and job vocabulary)."""
    global _COMMON
    if _COMMON is None:
        words: set[str] = set()
        try:
            from faker.providers.company.en_US import Provider as Company
            from faker.providers.job.en_US import Provider as Job
            from faker.providers.lorem.en_US import Provider as Lorem
            words |= {w.lower() for w in Lorem.word_list}
            for group in list(Company.catch_phrase_words) + list(Company.bsWords):
                words |= {w.lower() for phrase in group for w in re.findall(r"[A-Za-z]+", phrase)}
            for job in Job.jobs:
                words |= {w.lower() for w in re.findall(r"[A-Za-z]+", job)}
        except Exception:
            pass
        _COMMON = words
    return _COMMON


def lower_words(text: str) -> set[str]:
    """Words the source writes in lower case somewhere (ordinary vocabulary of this document)."""
    return {w.lower() for w in _WORD.findall(text) if w[0].islower()}


def label_names(label: str, lower: set[str], names: set[str], organisation: str | None = None,
                statement: bool = False) -> list[str]:
    """The words of `label` that are NAMES (E-52, model off). Evidence of a name: capitalised inside the
    prose and never written in lower case (`names`), a known place, or - outside a statement - a
    capitalised word the document never writes in lower case that is not an abbreviation, not the
    field's standard vocabulary and has no English word ending ('Jojobera', 'Maithon'). The
    organisation's own words are its branding and stay. Statement line items ('Reserves & Surplus')
    are standard wording unless a word has prose or place evidence."""
    org = {w.lower() for w in _WORD.findall(organisation or "")}
    places = known_places()
    common = common_words()
    # prose capitalises ordinary words too ('Regulations', 'Neutral' in a disclaimer): name evidence
    # only counts for words that are not ordinary English
    names = {n for n in names if n not in common and n not in GENERIC_WORDS
             and not any(n.endswith(s) for s in ENGLISH_SUFFIXES)}
    text = str(label)
    # a phrase ('Downside is more than 0%') is wording, not a name: only strong evidence counts
    phrase = sum(w[0].islower() for w in re.findall(r"[A-Za-z]{2,}", text)) >= 2
    found = []
    for match in re.finditer(r"[A-Za-z][A-Za-z']*", text):
        word = match.group(0).strip("'")
        if word.lower().endswith("'s"):
            word = word[:-2]
        abbreviation = text[match.end():match.end() + 1] == "."
        if len(word) < 3 or word.isupper() or abbreviation or (word.islower() and not statement):
            continue
        stems = _stems(word)
        stems |= {s + "s" for s in stems}
        if stems & org:
            continue
        if stems & names or (stems & places and not stems & lower):
            found.append(word)
            continue
        if phrase or len(word) <= 3:              # 'Mid', 'Bn': abbreviations unless named in the prose
            continue
        if statement or stems & lower or stems & GENERIC_WORDS or stems & common_words() \
                or not word[0].isupper():
            continue
        if any(word.lower().endswith(s) for s in ENGLISH_SUFFIXES):
            continue
        found.append(word)
    return found
