# tests/llm/test_gateways_factory.py
"""make_gateways: one gateway per provider, disabled without keys, cache and prompt log under artifacts."""
from src.config import load_config
from src.llm import CloudflareImageClient, LLMGateway, make_gateways


def test_without_keys_the_gateways_are_disabled(tmp_path, monkeypatch):
    for name in ("GROQ_API_KEY", "CF_ACCOUNT_ID", "CF_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    gateways = make_gateways(load_config(artifacts_dir=str(tmp_path), llm_enabled=True))
    assert isinstance(gateways["text"], LLMGateway) and gateways["text"].enabled is False
    assert gateways["vision"].enabled is False and gateways["image"] is None


def test_with_keys_each_gateway_has_its_limiter_cache_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "t")
    gateways = make_gateways(load_config(artifacts_dir=str(tmp_path), llm_enabled=True))
    assert gateways["text"].enabled and gateways["vision"].enabled
    assert isinstance(gateways["image"], CloudflareImageClient)
    assert gateways["text"].limiter.limits.tpm == 8000
    assert gateways["text"].cache_dir == tmp_path / "llm_cache" / "groq"
    assert gateways["text"].log_path == tmp_path / "prompt_log.jsonl"
