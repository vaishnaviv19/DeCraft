# src/generation/_facts.py  -- the fact calculator lives in the Shared Kernel (core.facts)
"""Facts (M8.2). Implemented once in `src.core.facts` so the privacy audit computes source facts the
same way."""
from src.core.facts import compute_facts

__all__ = ["compute_facts"]
