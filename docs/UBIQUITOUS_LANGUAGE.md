# DeCraft Ubiquitous Language

Version 1.0 · 24 October 2026 · Companion to the DeCraft Implementation Specification v3.

These are the only names to use for these concepts in planning, reasoning traces, code identifiers, test names, logs and reports. One term means one thing. A new concept gets a new row in the same change that introduces it.

Source of these terms: the two DeCraft specification documents (the existing repository was not available when this file was written). M0 step 2.1 requires the coding LLM to add the terms used in the existing `src/blueprint/`, `src/planner/` and `scripts/` code. Where that code uses another name for a concept below, write "existing code: `<name>`" in the row; do not rename the code.

## 1. Run and pipeline

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Run | `pipeline.run` | One execution of DeCraft on one Source PDF with one Config. Identified by `run_id`. | States: `partial` → `ok` or `failed`. A failed Run still writes its Run Directory and Run Manifest. |
| Run Directory | `artifacts/runs/<run_id>/` | The folder holding every output and report of one Run. | Write-once per Run; never edited by a later Run. |
| Run Manifest | `RunManifest` | The record of a Run: source hash, seed, Config Hash and snapshot, package versions, LLM settings and Prompt Versions, output hashes, Gates, Limitations, Synthetic Flag. | Written at start (`partial`) and updated at the end; always present. |
| Stage | `pipeline` | One step of a Run: extract, infer, privacy plan, generate, privacy audit, write, render, QA. | Each Stage has a timeout and a Token Budget; its state is persisted so a rerun resumes at the first unfinished Stage. |
| Gate | validation, QA, M11 | A pass/fail condition a Run must meet (for example zero Chart/Table Mismatches). | `true` or `false` in the Run Manifest. Hard-gate tests may never be weakened. |
| Limitation | reports | A stated limit of what DeCraft guarantees (for example "synthetic data is not automatically anonymous"). | Listed in the Privacy Report and Run Manifest; never removed to make a report look better. |
| Golden Baseline | `tests/golden/` | The frozen `blueprint.json` and `document_plan.json` of the existing pipeline, taken before any change. | Updated only in an explicitly labelled commit. The golden test must stay green. |

## 2. Source and extraction

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Source PDF | input | The PDF the user supplies. Its content is untrusted data. | Read only. Its text is never followed as instructions. |
| Blueprint | existing `src/blueprint` | The existing structured description of the Source PDF (blocks, tables, layout, style). | Existing behaviour is frozen by the Golden Baseline; M1 may only add the `to_source_document` adapter and optional provenance. existing code: `src/blueprint/build_blueprint.py`, `blueprint.json`. |
| Document Plan | existing `src/planner` | The existing plan of sections, word targets and elements for the output document. | Read only for all new modules. existing code: `src/planner/build_plan.py`, `document_plan.json`. |
| Source Document | `SourceDocument` | The adapter view of the Blueprint used by new modules: document hash, Entity Name, Source Tables, text blocks. | Built once per Run by `to_source_document`; immutable afterwards. existing code: `blueprint.json`. |
| Source Table | `SourceTable` | One extracted table: header, Raw Cells, page, caption, context text, provenance, Table Quality. | Raw Cells are never overwritten; normalisation produces new values beside them. existing code: `blueprint.tables[]` (a Table Schema without cells); the cells are in the Source CSV. |
| Raw Cell | `SourceTable.raw_cells` | The original extracted string of a cell (for example `"$1,250.40"` or `"—"`). | Kept for the whole Run alongside the normalised value. |
| Provenance | `SourceRef` | Where a value came from: document hash, page, table, row, column, bbox, extraction method. | Attached to Evidence; never invented. |
| Extraction Grade | `ExtractionGrade` | The three-level reliability of one Source Table. | `good` (normal inference), `doubtful` (rules capped at `hypothesized`), `bad` (table skipped and logged). Can only be lowered after grading (for example by a Total Mismatch), never raised. |
| Table Quality | `TableQuality` | Extraction Grade plus the list of issues found. | Set by `grade_table`; issues are reported verbatim. |
| Entity Name | `SourceDocument.entity_name` | The name of the company or organisation the Source PDF is about, plus its branding. | Always preserved and exempt from regeneration (D-07). existing code: `entity.entity_name`. |

## 3. Inference

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Inference Report | `InferenceReport` | Everything inferred from the Source Document: Field Profiles, Rules, Relationships, Patterns, Text Facts, Table Quality, Reference Date, warnings. | Produced only by `inference.infer`; JSON round-trip safe; carries `schema_version`, `source_sha256` and `config_hash`. |
| Field Profile | `FieldProfile` | The description of one column: physical type, semantic type, unit, Scale, Decimals, Additive flag, counts, Missing Kinds, statistics, Extraction Grade. | Counts must be consistent (`n_missing ≤ n`, `n_unique ≤ n − n_missing`). |
| Physical Type | `FieldProfile.physical_type` | The storage type of a column. | One of `integer`, `float`, `text`, `date`, `period`, `boolean`, `empty`. |
| Semantic Type | `FieldProfile.semantic_type` | What a column means in the domain (currency, percentage, identifier, count ...). | Proposed by rules and the Annotator; uncertain values stay `unknown`. Values used so far: `currency`, `percentage`, `count`, `identifier`, `numeric`, `category`, `date`, `period`, `text`, `unknown`. |
| Base Units | `core/units.py` | Values stored without display scale (dollars, not millions). | All stored numbers are in Base Units; Scale is used only to parse and format. |
| Scale | `ScaleInfo`, `FieldProfile.scale` | The multiplier between displayed and Base Units (1e6 for "$ millions"). | Never applied to percentages or counts. |
| Decimals | `FieldProfile.decimals` | The most common number of displayed decimals in a column. | Sets Display Rounding and Formula tolerance. |
| Additive | `FieldProfile.additive` | True when the column's source Total Rows equal the sum of their children. | Additive columns are Rolled Up; non-additive columns on Total Rows are recomputed by their Formula or left blank. |
| Period | `core/periods.Period` | A typed time label (year, quarter, month; fiscal or calendar) with an ordinal. | Fiscal labels stay fiscal unless the year-end month is stated. |
| Cumulative Series | `detect_cumulative` | A series reported as year-to-date running totals. | Trend and break detection always run on de-cumulated values; generation re-cumulates. |
| Reference Date | `InferenceReport.reference_date` | The "as of" date used by date derivations such as Age from DOB. | Text date → latest Period end → none (then the rule is `semantic`). |
| Missing Kind | `MissingKind` | Why a cell has no value. | `null`, `dash`, `na`, `nm`, `suppressed_privacy`, `extraction_failed`. Never converted to zero, except `dash` when `dash_means_zero` confirms it. |
| Missingness Pattern | `MissingProfile.pattern` | How missing cells are placed in a column. | `none`, `leading`, `grouped`, `random`; reproduced in the Display Table after totals are computed. |
| Text Fact | `TextFact` | A number found only in the narrative text (for example "headcount 4,200"). | Linked to a table aggregate when it equals one (then derived); otherwise a root value under the Jitter Policy. |
| Annotator | `inference/_annotator.py` | The LLM agent that proposes column meanings and Proposals for one table in one batched call. | Output is validated against a strict schema; columns it invents are dropped; None means fall back to Python only. |
| Proposal | `Proposal` | A candidate relationship or Formula suggested by the Annotator or by `detect_total_columns`. | Never enforced directly; always verified and passed through `classify_rule`. Kinds: `formula`, `derived_from`, `determines`, `ordering`, `hierarchy`, `conditional`. |

## 4. Rules, evidence and decisions

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Rule | `Rule` | A verified statement about the data that may constrain generation (formula, ordering, total rows, aggregate, ...). Distinct from a Pattern. | Has exactly one Rule Tier, Decision Status and Enforcement, plus evidence and a reason. |
| Rule Kind | `RuleKind` | The structural type of a Rule. | `formula`, `date_derivation`, `determines`, `ordering`, `hierarchy`, `conditional`, `total_rows`, `aggregate`, `foreign_key`. |
| Rule Tier | `RuleTier` | How strongly the evidence supports a Rule. | `explicit`, `strongly_inferred`, `semantic`, `hypothesized`, `unknown`, `contradicted`. Set only by `classify_rule`. |
| Explicit | tier | The rule is stated in the Source PDF text and not contradicted by the data. | HARD. If contradicted, it is not enforced and its status is `needs_review`. |
| Strongly Inferred | tier | Match Rate ≥ 0.95 on at least 5 testable rows. | HARD (capped to `hypothesized` on a doubtful table). |
| Semantic | tier | Plausible but untestable (fewer than 5 testable rows, or no Reference Date). | HARD unless contradicted (D-03). |
| Hypothesized | tier | 0.80 ≤ Match Rate < 0.95. | Recorded, never enforced. |
| Unknown | tier | 0.50 ≤ Match Rate < 0.80. | Recorded, never enforced (D-16). |
| Contradicted | tier | Match Rate < 0.50, or the formula text is unparseable. | Rejected. |
| Decision Status | `DecisionStatus` | The review state of an inference. | `accepted`, `needs_review`, `rejected`, `insufficient_evidence`. |
| Enforcement | `Enforcement` | Whether generation and validation must honour a Rule. | `hard` (must hold; violation → Repair or HardConstraintError), `soft` (Statistical Target; unmet → Unmet Target), `none` (report only). |
| Rule Policy | `classify_rule` | The single function that maps a Verify Result, text evidence and Extraction Grade to tier, status and enforcement. | The only place enforcement is decided. It takes no LLM confidence input. |
| Verify Result | `VerifyResult` | The outcome of testing a Proposal on rows: Match Rate, rows tested, invalid rows. | `match_rate = None` means untestable. |
| Match Rate | `Rule.match_rate` | The share of testable rows on which a Rule holds within tolerance. | 0–1; mapped to tiers by the bands in Config. |
| Evidence | `Evidence` | One reason supporting or contradicting an inference, with optional Provenance. | Sources: `header`, `caption`, `cell_format`, `context_text`, `cross_table`, `stat_test`, `llm_proposal`, `rule_detector`, `extraction`. |
| LLM Confidence | `llm_confidence` | The model's self-reported certainty. | Used for escalation and reporting only; never for Enforcement; never called a probability. |
| Formula | `FormulaSpec` | A row-wise equation `[Output] = expression` stored as an Expr Tree. | Built only by `parse_formula`; evaluated only by `evaluate`; never by `eval`/`exec`/`compile`. |
| Formula Text | LLM output | The text form the LLM writes, with columns in square brackets. | Allowed: columns, numbers, `+ - * /`, parentheses, and `sum, min, max, abs, round, years_between, days_between, pct_change, ratio_pct`. Anything else → FormulaSyntaxError → `contradicted`. |
| Expr Tree | `Expr` | The parsed formula: nodes `col`, `const`, operators and allowed functions. | Serialisable; the output column may not appear among its inputs. |
| Rounding Tolerance | `rounding_tolerance` | The per-row allowed difference derived from displayed Decimals plus propagated input rounding. | Combined with `rel_tol_default` (whichever is larger). |
| Total Column | `detect_total_columns` | A column headed "total" that equals the row-wise sum of the other additive columns. | The only formula search allowed with the LLM off (D-05b). |
| Total Row | `core/hierarchy` | A row labelled total or subtotal that sums the rows above it at its level or deeper. | Never sampled; always Rolled Up. |
| Total Mismatch | `downgrade_for_total_mismatch` | Source Total Rows that do not reconcile. | Lowers a `good` table to `doubtful`. |
| Aggregate Rule | `AggregateRule` | A summary table column equal to an aggregate (sum, mean, count, max, min) of a detail table column per label. | The summary is computed from the generated detail, never sampled. |

## 5. Relationships and generation order

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Primary Key | `CanonicalDataset.key_columns` | A column that uniquely identifies rows (unique ≥ 0.98, missing ≤ 2%, identifier-like). | Generated as Fresh Keys; duplicates are a hard violation. |
| Foreign Key | `Relationship` | A child column whose values reference a parent Primary Key (overlap ≥ 0.9, compatible types). | Every generated child value must exist in the generated parent. Name similarity alone never creates one. |
| Relationship | `Relationship` | A typed link between two columns, with Cardinality, overlap and tier. | Kinds: foreign key (tables); semantic links are Rules. |
| Cardinality | `Cardinality` | The multiplicity of a Relationship. | `one_to_one`, `one_to_many`, `many_to_many`. |
| Column Node | `ColumnRef.node` | The graph name of a column: always `table.column`. | Bare column names are never graph nodes (B-02). |
| Dependency Graph | `generation/_graph.py` | A column graph (formula, foreign-key and aggregate edges from HARD rules only) plus a table graph. | Must be acyclic; cycles raise CyclicDependencyError with the path; missing inputs raise HardConstraintError. |
| Generation Plan | `build_plan` | The ordered list of Steps produced from the Dependency Graph. | Deterministic (lexicographic tie-break); identical across reruns. |
| Step | `Step.action` | One action in the Generation Plan. | `sample`, `fresh_key`, `copy_fk`, `derive`, `rollup`, `aggregate`. |
| Root Column | generation | A column produced by `sample` (no HARD rule produces it). | Generated by a distribution, the Jitter Policy, the trend generator or the copula. |
| Derived Column | generation | A column produced by a HARD Formula. | Never sampled; always recomputed after its inputs change. |
| Roll Up | `core/rollup.rollup` | Computing Total Rows bottom-up from leaf rows. | Runs after leaf generation for Additive columns. |

## 6. Patterns

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Pattern | `PatternSpec` | A statistical property of the source to preserve (trend, segments, correlation, seasonality, lag, segment pattern). Distinct from a Rule. | Always SOFT. Detected with sample-size guards; `uncertain` when evidence is thin. |
| Trend | `classify_trend` | The overall direction of a series from Kendall tau and a Theil–Sen slope. | `up`, `down`, `flat`, `mixed`, `uncertain` (fewer than 5 points). A rising Trend may contain dips. |
| Segment Shape | `SegmentShape` | A series described as 1–3 straight segments separated by Structural Breaks. | Tier `confirmed`, `probable` or `uncertain`; an uncertain shape falls back to the plain Trend. |
| Structural Break | `core/segments.py` | A persistent level or slope change confirmed by size, persistence, permutation test, leave-one-out and BIC. | Minimum segment length 3; false-positive rate on noise ≤ 5% (hard gate). |
| Cause Label | `cause_is_supported` | An LLM-proposed reason for a Structural Break, taken from nearby text. | Accepted only when a Break was detected at that Period; otherwise dropped. |
| Residual Correlation | `core/stats.correlation` | Spearman correlation measured after Theil–Sen detrending of time-indexed series. | `positive`, `negative`, `none`, `uncertain` (fewer than 8 pairs). |
| Statistical Target | SOFT enforcement | A Pattern the generator tries to reproduce. | Met, or recorded as an Unmet Target; never overrides a HARD Rule. |
| Unmet Target | `UnmetTarget` | A Statistical Target that the best of up to 50 attempts did not reach. | Recorded in the Validation Report; the Run continues (D-06). |

## 7. Privacy

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Sensitivity Class | `SensitivityClass` | The privacy class of a column. | `direct_identifier`, `quasi_identifier`, `sensitive`, `business_metric`, `low_risk`, `derived`. |
| Stricter-of Rule | `merge_risk` | The final class is the stricter of the rule detector and the LLM label. | The LLM can raise risk, never lower it. |
| Privacy Plan | `PrivacyPlan` | One Privacy Decision per column, plus the list of Tiny Tables. | Produced by `plan_privacy` before generation. |
| Privacy Decision | `PrivacyDecision` | Class, method, Noise Budget, importance, exemption and rationale for one column. | Exempt columns (Entity Name, branding) are preserved. |
| Noise Budget | `Policy.noise` | The relative jitter applied to fitted distribution parameters. | Never added to source values directly. |
| Label Map | `LabelMap` | A consistent mapping from source labels to fresh labels across all tables. | Never returns the source label, except for exempt labels. |
| Fresh Key | `generation/_keys.py` | A newly generated identifier in a format and range disjoint from the source. | Never equal to any source identifier. |
| Tiny Table | `tiny_table_max_rows` | A table with at most 4 rows. | Generated by the Jitter Policy, not a fitted distribution. |
| Jitter Policy | `generation/_tiny.py` | One factor per root numeric column from [0.60, 1.60] excluding 0.95–1.05, times a ±3% wobble. | Every cell ends at least 5% from its source cell. |
| Safe Factor | `safe_level_factor` | A random multiplier from the jitter band that is never within ±5% of 1.0. | Used for tiny tables and new series start levels. |
| Value Distance | `value_distance_violations` | The rule that each generated root numeric cell differs by at least 5% from its source counterpart (same row key and column). | Violation → repairable error. |
| Headline Distance | `headline_violations` | The rule that each number Fact differs by at least 10% from the same fact computed on the source. | Violation → repairable error; persistent → Run fails. |
| Quasi-identifier Match | `quasi_row_matches` | A generated row equal to a source row on all quasi-identifiers. | Not allowed (repairable error). |
| Rare Combination | `rare_combination_violations` | A quasi-identifier combination seen fewer than k=3 times. | Checked only on tables with at least 20 rows (warning). |
| Privacy Report | `PrivacyReport` | Checks run, violations and Limitations after generation. | Always states that synthetic data is not automatically anonymous and that no differential privacy is claimed. |
| Synthetic Flag | PDF metadata, `RunManifest.synthetic` | The record that the output data is generated. | Written as `decraft:synthetic=true` in PDF metadata and in the manifest only (D-07). |

## 8. Generation, validation and repair

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Canonical Dataset | `CanonicalDataset` | The single validated dataset: full-precision tables, Display Tables, keys, HARD rules, Facts, Chart Specs, lineage, validation result. | The only source for narrative, tables and charts. Produced only by `generation.generate`. |
| Display Table | `CanonicalDataset.display` | A rounded, tokenised copy of a table exactly as the reader sees it. | Tables, charts and Facts read from Display Tables. |
| Display Rounding | `core/rounding.display_round` | Rounding once, half up, at the column's Decimals. | Never Python's built-in `round` (banker's rounding). |
| Total Mode | `detect_total_mode` | How the source shows totals after rounding. | `sum_of_displayed` or `true_total` (with the footnote "figures may not sum due to rounding"). |
| Lineage | `CanonicalDataset.lineage` | For each Column Node, the method or Rule that produced it. | Written to `lineage.json`. |
| Validation Report | `ValidationReport` | Violations, Unmet Targets and Repair Records of one validation. | `ok` only when no error-severity Violation remains. |
| Violation | `Violation` | One failed check, with check name, Rule ID, severity, location and whether it is repairable. | Severity `error`, `warning` or `info`. Messages never contain raw sensitive values. |
| Repair | `validation/_repair.py` | A deterministic change to data that fixes repairable Violations. | Order: normalise → clip or resample → recompute derived → regenerate keys or rows → full revalidation. Never weakens a Rule. |
| Bounded Repair | `run_bounded_repair` | Up to `max_repairs` (3) Repairs, each followed by full validation. | The verdict comes from the validation after the last Repair; failure → HardConstraintError with the report. |
| Repair Record | `RepairRecord` | Attempt number, action, Rule IDs, rows affected and result of one Repair. | Kept in the Validation Report. |
| Utility Gate | `validation/_utility.py` | The pass/fail check that generated data keeps the source's useful structure. | Fail: confirmed trend flips, or a strong correlation changes sign. Warn: range, category share or correlation-strength drift. |

## 9. Narrative, charts and rendering

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Fact | `Fact` | A keyed value computed by Python from Display Tables (number, text or word). | The only way data enters the narrative. Number Facts are subject to Headline Distance. |
| Fact Store | `CanonicalDataset.facts` | All Facts of a Run, keyed by placeholder key. | Read only for writers. |
| Placeholder | `{{key\|unit:decimals}}`, `\|text`, `\|word` | A reference to a Fact inside a narrative template. | Unknown keys raise; any `{{` left after resolution fails QA. |
| Stray Number | `stray_numbers` | A digit written in a narrative template outside a Placeholder. | Not allowed: the draft is rejected and retried, then the fallback template is used. |
| Direction Word | word Fact | The wording of a Trend or segment ("increased", "declined", ...). | Chosen from the PatternSpec, never by the LLM. |
| Section Writer | `narrative/_writer.py` | The LLM agent that writes one section as a placeholder template. | Receives Fact keys and labels, never values; falls back to deterministic templates. |
| Chart Spec | `CanonicalDataset.charts` | A registered chart: table, label column and series columns. | A chart can only be drawn from a registered Chart Spec; its data is read from the Display Table. existing code: `paired_chart_spec` (Document Plan), `figures_charts[]` (Blueprint). |
| Chart/Table Mismatch | `assert_chart_matches_table` | A chart series value that differs from its Display Table cell. | Must be zero (Gate). |
| Rendered-PDF QA | `rendering/_qa.py` | Checks on the final PDF: pages rendered, page count, overflow, placeholders, word count, captions and units, chart sidecars. | Anything needing human judgement → Manual Review Required. |
| Manual Review Required | `QAResult.manual_review_required` | A check the system cannot pass automatically (for example visual similarity without a multimodal model). | Reported; never silently passed. |

## 10. LLM and configuration

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| LLM Gateway | `LLMGateway` | The only path to a language model: cache, retries with a repair hint, timeout, Token Budget, disabled mode, call log. | `ask_json` returns a validated object or None; it raises only `LLMBudgetExceeded`. |
| Prompt Spec | `PromptSpec` | A named, versioned system prompt (for example `annotator` `v2`). | Any text change bumps the Prompt Version (and changes the cache key). |
| Prompt Version | `PromptSpec.version` | The version label of a Prompt Spec. | Recorded in the Run Manifest. |
| Token Budget | `budgets.stage_token_budget` | The estimated token allowance per Stage. | Exceeding it raises `LLMBudgetExceeded`; the Stage then falls back. |
| Fallback Ladder | `pipeline` | Multimodal model → text model → deterministic templates. | The deterministic path must always complete. |
| LLM-off Mode | `llm_enabled: false` | A Run with no model calls. | Must complete with template narrative; only Total Columns and Total Rows are found as formulas. |
| Config | `src/config.py`, `config/default.yaml` | The single strict configuration (seed, Thresholds, budgets). | YAML and code defaults must be equal; unknown keys are rejected. |
| Thresholds | `Config.thresholds` | All numeric limits (tier bands, sample guards, privacy distances, utility tolerances). | Only changed in Config, never as literals in code. |
| Config Hash | `Config.config_hash()` | The sha256 of the canonical JSON of the Config. | Recorded in the Inference Report and Run Manifest. |
| Seed | `Config.seed` | The single source of randomness (42 by default). | Every random draw uses a passed `np.random.Generator`; global random state is forbidden. |

## 11. Engineering method

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Deep Module | `src/<module>/` | A module with a small public interface in `__init__.py` over a hidden implementation. | Other modules import only the public interface; underscore files are internal. |
| Public Interface | `__init__.py`, `__all__` | The functions and types a Deep Module exposes. | Changes only where the milestone boundary table allows. |
| Shared Kernel | `src/core`, `src/contracts` | Pure functions and data contracts used by several modules. | No I/O, no LLM; contracts change additively only. |
| Module Boundary | spec section 2.4 | The rule of which interfaces may change in a milestone. | Code changes must not leak across it. |
| TDD Cycle | spec section 2.2 | Red (one failing test) → Green (minimum code) → Refactor (review and clean). | One behaviour per cycle; tests are never weakened. |
| Interface Test | `tests/<module>/` | A test through a module's Public Interface. | May not be weakened during refactors. |
| Internal Test | `tests/<module>/internal/` | A test of an internal function. | May be rewritten when internals are refactored. |
| Fixture | `tests/fixtures/<domain>/` | A small source dataset with a hand-written expected inference. | At least 3 domains plus an unknown-domain fixture. |
| Hard-gate Test | tests | A test protecting a Gate (false-break rate, mismatches, privacy leaks, repair bound). | Fixed only by changing code or Config, never the assertion. |
| Decision Label | spec section 3 | An identifier D-xx, B-xx or G-xx for a binding decision, fixed bug or closed gap. | Cited in code comments where implemented. |

## 12. Existing pipeline terms (added in M0 step 2.1)

Terms used by `src/blueprint/`, `src/planner/` and `scripts/` that the sections above did not yet cover. They describe the frozen extraction and planning stages; new modules read them and never change them.

| Term | Context | Definition | Allowed States/Actions |
| --- | --- | --- | --- |
| Text Block | `blueprint.text_blocks` | One styled span of extracted text: page, `block_id`, text, font name, font size, font weight, colour, Bounding Box and origin. | Content is untrusted data. existing code: `extract_text_blocks`. |
| Bounding Box | `text_blocks[].bbox` | The rectangle of a Text Block or Figure on its page, in PDF points (`x0`, `y0`, `x1`, `y1`). | Read only; used as Provenance. |
| Heading | `blueprint.headings` | A Text Block promoted to a Heading Level from font-size and weight clustering only (no keyword lists). | Level is `H1`, `H2` or `H3`; carries a `confidence`. existing code: `detect_headings`. |
| Heading Level | `blueprint.headings[].level` | The rank of a Heading. | `H1`, `H2`, `H3`. |
| Page Structure | `blueprint.page_structure` | One record per page: page size, margins, Running Header/Footer, page-number format and Column Layout. | Read only. existing code: `extract_page_structure`. |
| Page Margin | `page_structure[].margins` | The smallest text offset from each page edge, in points (`left`, `top`, `right`, `bottom`). | Used to place blocks and to detect overflow in Rendered-PDF QA. |
| Running Header | `page_structure[].header` | The repeated top-of-page text of one page, with its Bounding Box when known. | `null` when absent. |
| Running Footer | `page_structure[].footer` | The repeated bottom-of-page text of one page, with its Bounding Box when known. | `null` when absent. |
| Column Layout | `page_structure[].column_layout` | The number of text columns on a page. | `single`, `multi`, `unknown`. |
| Colour Palette | `blueprint.color_palette` | The dominant and accent colours of the Source PDF plus the raw colour counts. | Read only for rendering style. existing code: `extract_palette`. |
| Dominant Colour | `color_palette.dominant` | Up to four colours that cover most non-black text and raster pixels. | Hex strings. |
| Accent Colour | `color_palette.accent` | Up to four further colours used for emphasis. | Hex strings. |
| Figure | `blueprint.figures_charts` | An image extracted from the Source PDF: `figure_id`, image path, page location, Bounding Box, Chart Type and the classification method. | Read only. existing code: `extract_charts`. |
| Chart Type | `figures_charts[].chart_type` | The shape of a Figure, from a heuristic or a vision model. | `bar`, `line`, `pie`, `other`. |
| Table Schema | `blueprint.tables[]` | The header and per-column description of one Source Table (no cell values): table id, Source CSV path, Column Headers, counts, Inferred Column Types. | Read only; the cells come from the Source CSV. existing code: `extract_table_schemas`. |
| Column Header | `tables[].column_headers` | The header labels of one Source Table, in column order. | Any string, including empty. |
| Inferred Column Type | `tables[].columns[].inferred_type` | The cell type the Blueprint observed in a column, before inference. | `integer`, `float`, `string`, `currency`, `percentage`, `numeric_with_units`, `empty/null`. |
| Source CSV | `tables[].source_csv` | The CSV holding the Raw Cells of one Source Table, written by the extraction stage. | Read only; original strings kept verbatim. |
| Reconstructed Table | `outputs/structured/reconstructed_tables/` | The merged-cell-aware table CSVs the Blueprint prefers over the plain `tables/` ones, with a manifest of page, row, column and merged-cell data. | The manifest supplies the page number of a Source Table. existing code: `manifest.json`. |
| Blueprint Validation | `blueprint.validation` | The FR-A1 check of the Source PDF (exists, readable, unencrypted, at least one page) with its metadata. | Read only. existing code: `validate_pdf`. |
| Section | `document_plan.sections` | One planned part of the output document: id, title, purpose, Target Word Count, Required Components, tables, charts and source page. | Read only for all new modules. existing code: `build_document_plan`. |
| Section Purpose | `sections[].purpose` | The one-sentence job of a Section. | Non-empty text. |
| Target Word Count | `sections[].target_word_count` | The number of words the Section Writer must aim for. | Integer; the Document Plan total is at least 1,000 (safety margin 1,500). |
| Required Component | `sections[].required_components` | The element types a Section must contain. | `narrative`, `table`, `chart`. |
| Words per Page Target | `document_plan.words_per_page_target` | The source word count per page that the plan reproduces. | Read only. |
| Paired Chart Requirement | `sections[].paired_chart_requirements` | The binding rule that every planned table declares its Chart Spec. | Enforced by `enforce_rules`; one per planned table. |
| Plan Constraint | `document_plan.constraints` | The rules the plan guarantees (no verbatim copy, same table count, one chart per table, same words per page). | Read only. existing code: `enforce_rules`, `validate_plan`. |
| Run Script | `scripts/run_blueprint_and_plan.py` | The existing entry point that builds the Blueprint and the Document Plan for one Source PDF. | Behaviour frozen by the Golden Baseline; `scripts/run_decraft.py` is added in M9. |

