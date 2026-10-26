"""Live smoke test for the three providers (M8.0.3).

Needs GROQ_API_KEY, CF_ACCOUNT_ID and CF_API_TOKEN in the environment. It sends one tiny JSON request
per provider and one small image request, and prints the model ids and (where available) the quota
headers. Run it from the repository root:

    python scripts/smoke_llm.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel  # noqa: E402

from src.config import load_config  # noqa: E402
from src.llm import PromptSpec, make_gateways  # noqa: E402


class Ok(BaseModel):
    ok: int = 1


def small_jpeg() -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def main() -> int:
    cfg = load_config(llm_enabled=True)
    gateways = make_gateways(cfg)
    prompt = PromptSpec("smoke", "v1", 'Return ONLY JSON: {"ok": 1}.', max_input_tokens=600,
                        max_output_tokens=32, rationale="M8.0.3 live smoke test")

    text = gateways["text"].ask_json(prompt, 'Reply with {"ok": 1}', Ok)
    print(("text OK " + cfg.providers.text.model) if text else "text FAILED (missing GROQ_API_KEY?)")

    vision = gateways["vision"].ask_json(prompt, 'Return {"ok": 1} for this image.',
                                         Ok, images=[small_jpeg()])
    print(("vision OK " + cfg.providers.vision.model) if vision
          else "vision FAILED (missing CF_ACCOUNT_ID / CF_API_TOKEN?)")

    image_client = gateways["image"]
    if image_client is None:
        print("image SKIPPED (missing CF_ACCOUNT_ID / CF_API_TOKEN)")
        image_ok = True
    else:
        data = image_client.generate("a plain white square", seed=1,
                                     steps=cfg.providers.image.steps)
        print(f"image OK {len(data)} bytes")
        image_ok = bool(data)

    return 0 if (text and vision and image_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
