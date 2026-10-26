# tests/test_prompt_budgets.py -- every prompt leaves room for its payload, and fits the provider's minute budget
import pytest
from src.llm._payload import estimate_tokens
from src.narrative import _prompts as narrative_prompts
from src.rendering import _prompts as rendering_prompts
from src.style import _prompts as style_prompts
from src.inference._annotator import ANNOTATOR_PROMPT
from src.privacy._labels import LABELS_PROMPT

PROMPTS = narrative_prompts.ALL + rendering_prompts.ALL + style_prompts.ALL + [ANNOTATOR_PROMPT, LABELS_PROMPT]
TEXT_TPM = 8000          # Groq free tier tokens/minute for the text model (config: providers.text.tpm)


@pytest.mark.parametrize('p', PROMPTS, ids=lambda p: p.name)
def test_system_prompt_leaves_at_least_half_the_input_budget(p):
    assert estimate_tokens(p.system) <= p.max_input_tokens // 2


@pytest.mark.parametrize('p', [p for p in PROMPTS if p.role == 'text'], ids=lambda p: p.name)
def test_one_text_call_fits_the_minute_budget(p):
    assert p.max_input_tokens + p.max_output_tokens <= TEXT_TPM


def test_prompt_names_unique_and_versioned():
    names = [p.name for p in PROMPTS]
    assert len(names) == len(set(names)) and all(p.version.startswith('v') for p in PROMPTS)


def test_every_prompt_has_a_rationale_for_the_log():
    assert all(p.rationale for p in PROMPTS if p.name != 'annotator')


def test_every_prompt_demands_json_only():
    assert all('ONLY JSON' in p.system or 'ONLY one JSON' in p.system for p in PROMPTS)


@pytest.mark.parametrize('p', [p for p in PROMPTS if p.role == 'vision'], ids=lambda p: p.name)
def test_every_vision_prompt_fits_at_least_one_full_size_image(p):
    """E-40: one 1024 px image costs ~6,600 tokens on the vision model (measured 2026-10-26); a smaller
    cap would refuse every call."""
    import io
    from PIL import Image
    from src.llm._payload import image_tokens
    buffer = io.BytesIO()
    Image.new("RGB", (1024, 1024), "white").save(buffer, format="JPEG")
    assert p.max_input_tokens >= image_tokens(buffer.getvalue()) + estimate_tokens(p.system)
