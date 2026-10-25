# Blueprint extraction module.
# The only M1 addition is the additive adapter to_source_document; existing behaviour is frozen by
# the Golden Baseline (tests/pipeline/test_golden.py).
from src.blueprint.to_source_document import to_source_document

__all__ = ["to_source_document"]
