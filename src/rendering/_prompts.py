# src/rendering/_prompts.py   -- vision prompts used on the OUTPUT
from src.llm import PromptSpec

CRITIC = PromptSpec(
    name="visual_critic", version="v3", role="vision", temperature=0.0, max_input_tokens=14500,
    max_output_tokens=700, reasoning_effort=None,
    rationale="Deterministic judgement; may only propose changes to the listed parameters (decision E-18).",
    system=("The image shows two pages side by side: LEFT is a SOURCE document page, RIGHT the GENERATED page that must look like it was published by "
            "the same designer. Content differs on purpose: judge only layout and style (spacing, "
            "chart and table proportions, legend placement, overflow, clipping, overlaps). Font sizes are fixed to the source and must not be changed. 'params' lists what may "
            "change with current value and allowed range. Return at most 6 issues, most visible first, each with a "
            "param from 'params' (or 'none') and a value inside its range. score: 10 = indistinguishable. Return "
            'ONLY JSON: {"score":int,"issues":[{"block_id":str|null,"problem":str,"param":str,"target":str|null,'
            '"value":number|str|null}]}'))

IMAGE_CHECK = PromptSpec(
    name="image_check", version="v1", role="vision", temperature=0.0, max_input_tokens=7500,
    max_output_tokens=300, reasoning_effort=None,
    rationale="Checks a generated photo against its caption (decision E-20).",
    system=("Does the image match the caption in <data></data>? List caption elements that are clearly missing or "
            'contradicted. Ignore style. Return ONLY JSON: {"matches":bool,"missing":[str]}'))

DISCRIMINATOR = PromptSpec(
    name="discriminator", version="v2", role="vision", temperature=0.0, max_input_tokens=14500,
    max_output_tokens=200, reasoning_effort=None,
    rationale="Measures 'indistinguishable' (task 9, v3.3 F-09): asked twice with swapped order to cancel "
              "position bias. Input cap = two page images at the measured image cost (E-40), like the critic.",
    system=("Two pages are shown side by side: LEFT is A, RIGHT is B. Exactly one is a page from a professionally published original; the "
            "other was produced by imitating it. Judge only design quality and publishing polish (typography, "
            "alignment, spacing, chart and table finish), not the content. Return ONLY JSON: "
            '{"original":"A"|"B","reason":str}'))

ALL = [CRITIC, IMAGE_CHECK, DISCRIMINATOR]
