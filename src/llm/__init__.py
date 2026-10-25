# src/llm/__init__.py  -- PUBLIC INTERFACE of the llm module (deep module)
"""Everything that talks to a language model goes through LLMGateway.ask_json.
Hidden inside: prompt versioning, response cache, retries with repair hint, code-fence stripping,
strict schema validation, timeouts, token budgets, disabled mode, call log."""
from src.llm._clients import LLMUnavailable, OllamaClient, default_gateway
from src.llm._gateway import LLMClient, LLMGateway, PromptSpec, CallRecord

__all__ = ["LLMClient", "LLMGateway", "PromptSpec", "CallRecord", "OllamaClient", "LLMUnavailable",
           "default_gateway"]
