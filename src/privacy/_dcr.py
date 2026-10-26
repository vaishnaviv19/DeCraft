# src/privacy/_dcr.py   (v3.3 F-02) -- the implementation lives in the Shared Kernel (src/core/dcr.py) so that
# validation can use it without importing privacy internals (module boundaries, test_guards).
from src.core.dcr import dcr, dcr_violations, identical_match_share  # noqa: F401
