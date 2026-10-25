# src/generation/_keys.py  -- Fresh Keys live in the Shared Kernel; this is the generation module's view
"""Fresh Key generation (M5.1). Implemented once in `src.core.keys` and re-exported here."""
from src.core.keys import fresh_key_values

__all__ = ["fresh_key_values"]
