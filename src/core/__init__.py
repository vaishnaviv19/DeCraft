# src/core/__init__.py  -- SHARED KERNEL: pure functions, no I/O, no LLM
"""Shared Kernel building blocks used by several modules.

Modules import the submodule they need, for example `from src.core.units import parse_scale`.
Nothing here performs I/O or calls a model.
"""
