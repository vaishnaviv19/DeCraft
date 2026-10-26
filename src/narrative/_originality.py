# src/narrative/_originality.py
"""Spec S.7 location; the helpers live in src/core/originality.py so the privacy module (Label
Fabricator, E-35) and the narrative module share one implementation."""
from src.core.originality import (CONTINENTS, GENERIC, LINE_ITEMS, MONTHS, PROPER_RE, STOP,  # noqa: F401
                                  check_label_map, is_generic_label, name_words, names_in, reused_names,
                                  source_proper_nouns, tone_profile)
