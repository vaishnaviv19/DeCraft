# tests/llm/test_providers.py  -- request shapes, checked with httpx.MockTransport (no network)
import base64
import json

import httpx
import pytest

from src.llm._providers import CloudflareImageClient, CloudflareVisionClient, GroqClient


def mock_post(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", post)


def test_groq_request_carries_model_json_mode_and_reasoning(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"x": 1}'}}],
                                         "usage": {"total_tokens": 3}})

    mock_post(monkeypatch, handler)
    client = GroqClient("openai/gpt-oss-120b")
    assert client.complete("sys", "user", reasoning_effort="low") == '{"x": 1}'
    assert seen["body"]["model"] == "openai/gpt-oss-120b"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["reasoning_effort"] == "low"
    assert seen["auth"] == "Bearer test-key"
    assert client.last_usage == {"total_tokens": 3}


def test_groq_retries_once_without_reasoning_effort_on_400(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, text="reasoning_effort is not supported by this model")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    mock_post(monkeypatch, handler)
    assert GroqClient("m").complete("s", "u", reasoning_effort="low") == "{}"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


def test_groq_is_text_only(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with pytest.raises(ValueError):
        GroqClient("m").complete("s", "u", images=[b"x"])


def test_cloudflare_vision_sends_the_image_as_a_data_url(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "token")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"result": {"response": {"ok": 1}, "usage": {"prompt_tokens": 1640}}})

    mock_post(monkeypatch, handler)
    client = CloudflareVisionClient("@cf/meta/llama-3.2-11b-vision-instruct")
    reply = client.complete("s", "describe", images=[b"\xff\xd8jpeg"])
    content = seen["body"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "accounts/acct/ai/run/@cf/meta/llama-3.2-11b-vision-instruct" in seen["url"]   # native endpoint
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert json.loads(reply) == {"ok": 1} and client.last_usage["prompt_tokens"] == 1640    # parsed object -> text


@pytest.mark.parametrize("size,tokens", [((256, 256), 1650), ((560, 792), 3300), ((1024, 1024), 6600),
                                         ((3000, 3000), 6600)])
def test_image_tokens_follow_the_measured_tiles(size, tokens):
    import io
    from PIL import Image
    from src.llm._payload import image_tokens
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="JPEG")
    assert image_tokens(buffer.getvalue()) == tokens
    assert image_tokens(b"not an image") == 6600


def test_cloudflare_image_returns_bytes_and_sends_steps_but_no_seed(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "token")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": {"image": base64.b64encode(b"JPEG").decode()}})

    mock_post(monkeypatch, handler)
    client = CloudflareImageClient()
    assert client.generate("a plain wall", seed=7, steps=4) == b"JPEG"
    assert seen["body"] == {"prompt": "a plain wall", "steps": 4} and client.last_seed == 7   # API rejects seed
