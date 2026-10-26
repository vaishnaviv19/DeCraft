# Prompt changes

One note per prompt version change (E-24). The full text of each saved version is in
`docs/prompts/<name>_<version>.txt` (E-24a). `scripts/build_prompt_log.py` (M10.1) renders these notes into
`PROMPT_LOG.md` next to the failure counts from `artifacts/prompt_log.jsonl`.

## annotator

| Version | Change | Reason |
| --- | --- | --- |
| v2 | Starting version (v3 spec, Appendix R). | — |
| v3 | Each column also returns `sensitivity_reason`; sensitivity classes defined by meaning, with examples from several fields (finance, biology, medicine), and "a unique code is a direct identifier only if it points at a real person or organisation". | Privacy labels were keyword-driven; a biology table (gene and sample codes) was labelled as personal identifiers. Text not saved (only v2 and v5 survive in git). |
| v4 | New role `row_label` for columns that name each row of a statement. | Generation re-drew statement line items at random (Revenue, Gross profit … repeated or lost). Text not saved. |
| v5 | Receives all row labels of statement tables and proposes `row_formula` relations between rows ("[Gross profit] = [Revenue] - [Cost of sales]"). | Row arithmetic could not be expressed or found. Saved: `annotator_v5.txt`. |

## cross_table_linker (E-31)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | New: one call per document proposes links between tables and summary totals; Python tests each pair on all rows. Budget fields and rationale added without a text change. | Replaces the try-every-column search when the LLM is on. |

## break_causes (E-31)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | New: names the cause of a confirmed structural break, kept only when its quote is in the text. | — |
| v2 | "from a company document" → "from a document". | E-25: no document type or organisation kind in any prompt. |

## entity_identity (E-31)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | New: the organisation's name and address, each kept only when written verbatim in the document. | Text not saved. |
| v2 | Also returns the organisation's location (style hint for fabricated values). | Fake names and places did not fit where the organisation is based. |
| v3 | "a company document" → "a document". | E-25. |

## fake_values (retired: merged into `label_fabricator` v2, E-35)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | New: believable replacement values for identifying text columns, checked by Python. | Text not saved. |
| v2 | Asked in rounds (already-given values sent back), pattern + parts mode for large columns, locale. | v1 capped at 60 values; the rest became numbered labels. |

## privacy_methods (E-31, E-34)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | New: the former `src/agents/privacy_agent.py` advice (method, noise, keep correlation, criticality) through the gateway. | Connect the old agent to the new pipeline. |
| v2 | "for synthetic decoy documents" → "for fabricated documents". | E-25 wording. Python additionally refuses `preserve` for any non-exempt column (E-34). |

## section_template (formerly `section_writer`)

| Version | Change | Reason |
| --- | --- | --- |
| v0 | The pre-v3.2 writer, saved as `section_template_v0.txt`; it was named `section_writer` v2 and clashed with the v3.2 `section_writer` v2. | One name and version must mean one text (E-24a). |
| v1 | Renamed to `section_template`; "a company report" → "a document about the organisation in the data". | E-25; retired when the v3.2 writer (M8.7) is in place. |

## label_fabricator (E-27, E-35, E-39)

| Version | Change | Reason |
| --- | --- | --- |
| v1 | Spec text (Appendix S.6). | — |
| v2 | Merged with `fake_values`: every text label of the document (each distinct label once), column meanings (`cols`), locale (`loc`), a pattern-and-parts mode (`pat`) for columns with more than 120 labels, and `errors` on the retry. "Generic vocabulary" widened to the standard vocabulary of the label's field; every name (segment, plant, place, person, organisation) is replaced; prefixes and unit suffixes are kept. Lives in `src/privacy/_labels.py`. | E-35: one agent for labels; E-39: the fixed generic list would have replaced 151 of the sample's 159 labels, standard terms included. |

A second `label_fabricator` v1 (different text) was added in `src/generation/_prompts.py` for M8.4 and
removed on 2026-10-26: one name and version must mean one text (E-24a), and M8.4 now reuses the
privacy plan's Label Fabricator decisions (E-35, E-39).

## v3.2 prompts (Appendix S.6)

`outline_titles` v2, `story_bible` v2, `section_writer` v2, `shorten` v1, `reconciler` v1,
`figure_analyst` v1, `chart_data_recovery` v1, `visual_critic` v2, `image_check` v1: brought in from the spec
unchanged; their v1 texts (where v2 exists) are in the v3.1 addendum. Input caps of the four vision prompts
were raised to the measured image cost without a text change (E-40): `visual_critic` 3,200 → 14,500,
`figure_analyst` 2,600 → 8,200, `image_check` 1,600 → 7,500, `chart_data_recovery` 1,800 → 7,500.

## outline_titles v3 (2026-10-26)

| Version | Change | Reason |
| --- | --- | --- |
| v2 | Spec text (Appendix S.6); reply `{"report_title","sections"}`. | — |
| v3 | Also returns `dt`: the document's type in 2-4 plain words, judged only from `p1` (the opening words of page 1). | E-25 says `dt` comes from the Titles call input, but v2 never asked for it, so every writer, the bible and the image prompts got an empty `dt` (and the image stage hardcoded "business report"). |

## section_writer v3 (2026-10-26, v3.3 F-03)

| Version | Change | Reason |
| --- | --- | --- |
| v2 | Placeholders `{{key}}`, `{{key|bn:1}}`, `{{key|m:1}}`, `{{key|pct:1}}`, `|text`, `|word`. | — |
| v3 | Numbers are written `{{key|auto}}` (or `|text`, `|word`); the writer must not type scale, unit or currency words next to a placeholder. | v2 let the writer type scale words; v3 delegates them to `|auto` (F-03). The repo's resolver never divided by `bn`/`m`, so `{{x|bn:1}} billion` would have printed a crore figure followed by "billion". |

Same day (E-48, before either v3 was used live): `section_writer` v3 also states that `w[1]` is a hard maximum ("NEVER more than w[1] words"), and `outline_titles` v3 gives each section a `tw` word limit for its title. Reason: text over the source box's word count overflowed the box and was cut.

## discriminator v1 (2026-10-26, v3.3 F-09)

Spec text (Appendix T.5). Input cap 14,500 instead of 3,000: two page images at the measured image cost (E-40), as for `visual_critic`.

Same day (v3.3 F-11, before v3 was used live): `section_writer` v3 adds rule (8) "Do not restate any sentence listed in 'rep'" and the legend key `rep`; the field is only sent on the one retry of a section that repeated another.
