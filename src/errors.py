# src/errors.py
class DeCraftError(Exception): ...
class HardConstraintError(DeCraftError): ...      # an enforced (hard) rule cannot be satisfied
class CyclicDependencyError(DeCraftError): ...    # dependency graph has a cycle
class FormulaSyntaxError(DeCraftError): ...       # a formula text uses a construct outside the allowlist
class PrivacyViolationError(DeCraftError): ...    # a hard privacy check still fails after bounded repair
class LLMSchemaError(DeCraftError): ...           # LLM output invalid after all retries
class LLMBudgetExceeded(DeCraftError): ...        # timeout or token budget exhausted for a stage
class StageTimeout(DeCraftError): ...             # a Pipeline Stage exceeded its timeout
class RenderQAError(DeCraftError): ...            # a critical rendered-PDF check failed
