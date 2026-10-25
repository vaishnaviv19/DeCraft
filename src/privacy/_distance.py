# src/privacy/_distance.py  -- kept as the privacy module's view of the Shared Kernel distance checks
"""Distance checks (D-12, D-13). Implemented once in `src.core.distance` and re-exported here."""
from src.core.distance import (aligned, headline_violations, quasi_row_matches,
                               rare_combination_violations, rel_diff, value_distance_violations)

__all__ = ["aligned", "headline_violations", "quasi_row_matches", "rare_combination_violations",
           "rel_diff", "value_distance_violations"]
