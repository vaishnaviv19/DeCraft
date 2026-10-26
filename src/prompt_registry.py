# src/prompt_registry.py  -- the PromptSpec registry used by the M10 scripts (a top-level composition
# module: it may import every module's prompts, which a file inside src/llm may not; test_guards)
"""Collect every `PromptSpec` in one place without moving the prompt text.

The specs live next to the code that uses them (so a change is reviewed with its consumer).  This
module imports those modules and returns the specs keyed by prompt name; `scripts/archive_prompts.py`
and `scripts/build_prompt_log.py` both use it so they cannot drift apart.
"""
from __future__ import annotations

import importlib

from src.llm import PromptSpec

MODULES = (
    "src.inference._annotator",
    "src.inference._linker",
    "src.inference._patterns",
    "src.narrative._prompts",
    "src.narrative._writer",
    "src.privacy._agents",
    "src.privacy._labels",
    "src.rendering._prompts",
    "src.style._prompts",
)


def all_prompts() -> dict[str, PromptSpec]:
    """Return `{name: PromptSpec}`, newest (highest version) when a name appears twice."""
    found: dict[str, PromptSpec] = {}
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if isinstance(value, PromptSpec):
                previous = found.get(value.name)
                if previous is None or _version_key(value.version) > _version_key(previous.version):
                    found[value.name] = value
    return dict(sorted(found.items()))


def _version_key(version: str) -> int:
    digits = "".join(character for character in version if character.isdigit())
    return int(digits or 0)
