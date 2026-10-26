# DeCraft Milestone Reports

Companion to `DeCraft_Implementation_Spec_v3.md`. One section per milestone: what changed and why, the
files touched, the design decisions with their D-/B-/G- labels, the commands run and their real
output, the tests added, the known gaps and the next smallest task. Terms are the ones in
`docs/UBIQUITOUS_LANGUAGE.md`.

## Baseline (before any change)

- `python -m pytest -q` → 19 tests, all passing (config 1, contracts 5, LLM gateway 9, guards 3,
  legacy `tests/test_schema.py` 1).
- `python scripts/run_blueprint_and_plan.py` → 1,121 Text Blocks, 12 Table Schemas, Document Plan
  3,565 words, 5 pages.
- The repository already held a pre-spec draft (`src/inference/*.py` flat modules, `src/generator/`,
  `src/agents/`, `src/orchestrator.py`, `src/repair_tables.ipynb`). It is tracked in git, is not on
  the spec's module layout, defines its own contract types and uses `RuleKind` for Rule Tier and
  `confidence` for several different notions. It is left in place untouched; see "Known gaps".

## M0 — Baseline, contracts, configuration, LLM gateway

Status: **complete**. 91 tests pass.

### What changed

- **M0.0 Golden Baseline.** Added `tests/pipeline/test_golden.py`: it re-runs the existing Run Script
  and compares `outputs/blueprint/blueprint.json` and `outputs/plan/document_plan.json` with
  `tests/golden/` after normalising volatile keys (paths, timestamps, `blueprint_id`, `plan_id`).
  `tests/golden/` already existed and matched. (G-12)
- **M0.1 Config.** Completed `tests/contracts/test_config.py` with the four remaining tests (YAML
  equals code defaults, unknown key rejected, band order enforced, Config Hash changes). The
  implementation in `src/config.py` and `config/default.yaml` already satisfied them. (D-02)
- **M0.2 Errors / M0.3 Contracts / M0.4 LLM gateway.** Already present and green; the contract and
  gateway test files match Appendix R.3 and R.4 test-for-test. No change.
- **M0.5 Test infrastructure.** Aligned `tests/conftest.py` with R.1: `cfg` is the LLM-off Mode
  (`load_config(llm_enabled=False)`), `rng` uses `cfg.seed`, `sales_df` is the reference fixture.
  `pyproject.toml` now runs with `--strict-markers`.
- **M0.6 Guard tests.** Replaced the provisional guard file with the R.11 version: AST-based
  `eval`/`exec`/`compile` check (comments may mention them), cross-module underscore-import check,
  and global-random-state check. `EXEMPT = {blueprint, planner}` only, as the spec says; this
  strengthens the old version, which also exempted `agents`, `generator`, `narrative`, `rendering`.
- **M0 step 2.1 Ubiquitous Language.** `docs/UBIQUITOUS_LANGUAGE.md` now records the terms the
  existing pipeline uses: 25 new rows (Text Block, Heading, Page Structure, Colour Palette, Figure,
  Table Schema, Source CSV, Reconstructed Table, Section, Paired Chart Requirement, …) plus
  `existing code: <name>` notes on Blueprint, Document Plan, Source Table, Source Document, Entity
  Name and Chart Spec. The copy at the repository root is identical. No existing code was renamed.

### Commands and real output

```text
python -m pytest -q
........................................................................ [ 79%]
...................                                                      [100%]
91 passed
```

### Known gaps

- The Golden test re-runs the Script into `outputs/` (the Script has no output-directory argument).
  It therefore rewrites the tracked `outputs/` files on every run; they normalise equal to
  `tests/golden/`. A future Run Script argument can make this a temporary directory.
- The pre-spec draft modules listed in the baseline are still outside the Ubiquitous Language and
  outside the deep-module layout. They are unreachable from the new modules and will be retired
  when M2/M3 rebuild the inference internals (`src/inference/*.py` must then become `_*.py`).

## M1 — Extraction-aware profiling

Status: **complete** for M1.1–M1.7. 91 tests pass; the Golden Baseline is still green.

### What changed

| Task | File | Notes |
| --- | --- | --- |
| M1.1 Adapter | `src/blueprint/to_source_document.py`, `src/blueprint/__init__.py` | The one allowed `src/blueprint` change. Reads the Source CSVs with `dtype=str, keep_default_na=False`, so Raw Cells keep `"$1,250.40"` and `""` exactly. Page comes from `reconstructed_tables/manifest.json` (fallback 1). `context_text` is the page's Text Blocks. A missing Source CSV becomes an empty table, which grading marks `bad`, instead of an exception. The input Blueprint is not mutated. |
| M1.2 Table grading | `src/inference/_quality.py` | D-14 as in R.5, with two corrections (below). |
| M1.3 Units and Scale | `src/core/units.py` | R.6, plus `Rs.`/`₹` support, a table-stated-context parser and `currency_code`. |
| M1.4 Periods | `src/core/periods.py` | R.6, plus an `fiscal_year_end_month` that accepts "Year ended March" without a day. |
| M1.5 Missingness | `src/core/missingness.py` | R.6 verbatim. `src/core/hierarchy.py` was added because `dash_means_zero` needs `verify_links`; its total-row tests stay in M2.5. |
| M1.6 Field Profiles | `src/inference/_profile.py` | Physical Type, Semantic Type, unit, Scale, Decimals, Missing Kinds, statistics, Extraction Grade, `llm_confidence`, Evidence. |
| M1.7 `infer` | `src/inference/__init__.py` | Public interface: `infer(doc, cfg, gateway=None)`. Grades every table, profiles every non-`bad` table, records `bad` tables in `warnings`. No model call yet, so `llm_used` is `False`. |

### Design decisions and corrections to the reference code

- **Parse-failure bands were unreachable (M1.2).** `_numeric_like` returned "looks numeric" only when
  ≥50% of values parsed, so "more than 50% failures → `bad`" could never fire. The column is now
  judged numeric when ≥50% parse, or when a numeric-looking Column Header says it should be numeric
  and ≥20% parse. Plain text columns are never condemned.
- **`NUM_RE` accepts the source's real formats.** `Rs. 86,856`, `850bps`, `(140bps)` and `8.0x` are
  numbers, not parse failures; without this, real tables were graded `doubtful` in error.
- **One Scale per Source Table (M1.3).** Scale evidence is the table's own header row or its caption
  (`"Y.E March (Rs. cr)"`, `"Consolidated (cr)"`). Page prose is *not* trusted to set Scale: one page
  holds a P&L in Rs crore next to a ratios table in multiples, so a page-level "Rs. cr" would
  over-scale the ratios. Percentages, counts, identifiers and text are never scaled. A column whose
  values carry `x` (multiples) or whose header is a ratio keeps Scale 1.0. Values are stored in Base
  Units and statistics are computed there.
- **Mixed formats mean mixed numbers, not mixed types (M1.6).** A value that parses as a date, Period
  or boolean is not a "format failure"; otherwise every transposed table raised false warnings.
  `Unnamed: N` headers carry no keyword evidence, and one identifier-shaped value is not enough to
  call a column an identifier (60% must match, at least 3 values).
- **Semantic Type is gated by Physical Type.** A label column that happens to contain
  "Shareholding %" is not a percentage column. `Value Per Share (Rs.)` is currency, not percentage.

### Real-data check on `sample_input_1.pdf`

```text
tables 12   grades {'good': 12}   profiles 60   warnings 4
scaled: table_003/007/008/009 numeric columns at 1e7 (Rs cr), table_006 currency at 1e7
warnings: 4 mixed-format columns (table_002 unnamed columns, table_006 % Holding) — all rows that
          mix a Period label or a merged-cell sentence into a numeric column
```

The four warnings are genuine extraction artefacts (transposed and merged-cell rows), reported rather
than guessed.

### Tests added (72 new; 91 in total)

- `tests/blueprint/test_to_source_document.py` (7): Raw Cell strings, page from manifest and fallback,
  Entity Name/title/context text, document hash, missing CSV, Blueprint not mutated.
- `tests/inference/test_quality.py` (10): clean/ragged/duplicate header/OCR/parse-failure bands,
  B-09 single-column rule, merged cells, Total Mismatch downgrade.
- `tests/inference/test_profile.py` (20): currency, percentage, identifier, count, Scale from the
  table header, cell-level currency, multiples, Missing Kinds, dates, Periods, mixed formats, empty
  columns, text/% traps, transposed-label traps.
- `tests/inference/test_infer.py` (5): one Table Quality per table, `bad` table skipped and logged,
  hashes and `llm_used`, JSON round trip, grade propagation.
- `tests/core/test_units.py` (13), `tests/core/test_periods.py` (5),
  `tests/core/test_missingness.py` (7).

### Known gaps

- A table whose unit appears only in the page or section heading (the P&L on page 2, header row
  `"", Q1FY24, …`) keeps Scale 1.0. Guessing from page prose was rejected because it corrupted the
  ratios table. The M2 Annotator is the right place to propose a table-level unit with Evidence.
- Tables that are transposed or carry several units per row (table_002 mixes rupees and crore) get
  one Scale for the whole column; row-level units are not modelled yet.
- `pytest-cov` is not installed in `.venv`, so the M10 coverage gate cannot be measured yet.

### Next smallest task

**M2.1, red step:** write the first test of `tests/inference/test_formula.py` (R.5) —
`[Revenue] = [Units] * [Unit Price]` parses, evaluates and matches `sales_df` — then add
`src/inference/_formula.py` until it passes. Before that test, retire the pre-spec
`src/inference/*.py` flat modules into the deep-module layout (`_*.py`) listed in section 5.3, so the
module has exactly one set of contract types.

## M2 — Constraint inference

Status: **complete** (M2.1–M2.7). 157 tests pass; the Golden Baseline is still green.

| Task | File | What it does |
| --- | --- | --- |
| M2.1 Formula engine (D-05) | `src/inference/_formula.py` | Parses the LLM's formula text with `ast.parse(..., mode="eval")` (parse only, never executed) through a strict allowlist, builds an `Expr` tree, evaluates it over pandas Series, and verifies it on the rows with a tolerance from displayed Decimals. Division by zero is invalid, never a match. |
| M2.2 Annotator | `src/inference/_annotator.py` | One batched, versioned `annotator` v2 call per table through the LLM Gateway. Strict schemas; invented columns are dropped; table text is wrapped in `<data>` and `</data>` inside it is neutralised; failure returns `None` so Python-only inference continues. |
| M2.3 Verifiers and policy (D-03/04/16/17) | `src/inference/_verifiers.py` | `verify_age_from_dob`, `verify_determines`, `verify_ordering`, `verify_conditional`, `verify_formula_text`; `tier_from_result` (six tiers, `semantic` when untestable); `classify_rule` is the only place that sets tier, status and Enforcement, and it has no confidence parameter. Doubtful tables are capped at `hypothesized`; text-stated rules become `explicit` only when the data does not contradict them. |
| M2.6 Reference date (D-19) | `src/inference/_refdate.py` | Explicit "as at / year ended <date>" first (month-year means month end), then the latest period end using the fiscal year-end month, else `None`. Forecast labels (`FY25E`) are not used as the reference date. |
| M2.5 Totals and rounding | `src/core/rounding.py`, `src/core/rollup.py`, `src/inference/_totals.py` | Display Rounding (half up, never banker's), largest-remainder percentages, Total Mode detection, display frames, Roll Up, and Total Row links. A column is Additive when its Total Rows reconcile at ≥ 0.95. |
| M2.4 LLM-off formulas | `src/inference/_totals.py` | `detect_total_columns`: the only formula search allowed with the AI off — a numeric column headed "total" that equals the row-wise sum of the other numeric columns. |
| M2.7 Wiring | `src/inference/__init__.py`, `src/inference/_profile.py` (`table_frame`) | Builds a normalised frame (numbers in Base Units, dates, text) beside the Raw Cells, then annotates (if the LLM is on), adds total-column proposals, verifies every proposal on the rows, and stores a `Rule` with tier, status, Enforcement, Evidence and reason. No rule can be enforced without `classify_rule`. |

### Real-data check after M2 (LLM off)

```text
refdate 2024-03-31   llm False   rules 0   warnings 4
grades {'good': 12}
```

No rules is the expected LLM-off result for this PDF: none of its tables has a column headed
"total". Two behaviours were corrected after looking at the real tables:

- **Forecast periods no longer set the Reference Date.** `FY25E` made the reference date 2025-12-31;
  estimate labels are now excluded, giving the end of the latest actual period (Q1FY24 → 2024-03-31).
- **Only an exact "Total"/"Subtotal" label is a Total Row.** Without indent levels, `TOTAL_RE`
  also matched "Total Assets" and "Total asset T.O (x)", wrongly summing whole balance sheets,
  marking every column non-Additive and downgrading two good tables to `doubtful`. The safer rule
  keeps all 12 tables `good`; richer hierarchies can return when row indent positions are extracted.

Tests added (M2 total: 62 new; 157 in total): `test_formula.py` (15), `test_verifiers.py` (22),
`test_annotator.py` (4), `test_refdate.py` (4), `test_totals.py` (5), `test_hierarchy.py` (5),
`test_rounding.py` (4), `test_infer.py` (11).

Also fixed: `FormulaSpec.inputs` now lists columns in the order they appear (it was reversed), so a
Rule's `columns` and its formula text agree.

### Next smallest task

**M3.1, red step:** a Primary Key test on `sales_df` — `Ref` (unique, identifier-shaped, no missing)
is a key and `Region` is not — then the candidate-key detector behind it.

## M3 — Relationships and dependency graph

Status: **complete** (M3.1–M3.4 and M3b). 243 tests pass; the Golden Baseline is still green.

| Task | File | What it does |
| --- | --- | --- |
| M3.1 Candidate keys | `src/inference/_relationships.py` | A Primary Key needs unique ≥ 0.98, missing ≤ 2% and identifier shape (header keyword or ≥ 60% identifier-shaped values). A numeric measure is never a key just because it is unique. |
| M3.2 Foreign keys | `src/inference/_relationships.py` | A child column is an FK only when ≥ 90% of its distinct values exist in another table's candidate key and the types are compatible. Name similarity alone never creates one. Stores Cardinality (`one_to_one` / `one_to_many`), overlap, parent unique rate, tier and status through `classify_rule`. |
| M3.3 Cross-table aggregates | `src/core/aggregation.py` | Finds a summary column equal to sum/mean/count/max/min of a detail column per label, and applies the rule to generated data later. |
| M3.4 Dependency graph and plan (B-02, B-03) | `src/generation/_graph.py` | Qualified column nodes (`table.column`), a separate table graph (FK parent before child, detail before summary), formula/FK/aggregate edge kinds, cycle path reporting, missing-input reporting, a deterministic lexicographic plan, and `rollup` steps after leaf generation. Only HARD rules create edges. |
| M3.7 Wiring | `src/inference/__init__.py` | The Inference Report now carries `relationships` and aggregate Rules; aggregate Rules use the same `classify_rule` policy and carry the detail/summary parameters the graph needs. |

Tests added (17 new for M3; 243 in total): `test_relationships.py` (5: key on `sales_df`, no key from
a unique non-identifier column, duplicates disqualify, FK by overlap, similar names alone do nothing),
`test_aggregation.py` (3), `test_graph.py` (9: inputs before outputs, same name in two tables,
soft rules create no edges, deterministic plan, cycle path, missing input, rollup after leaves, FK
parent first, aggregate detail before summary).

Real-data check: `infer` on `sample_input_1.pdf` now takes 0.31 s and finds **0 relationships and
0 aggregate rules** — the conservative thresholds produced no false links on a report of 12 unrelated
tables.

Note: `_verifiers.py`, `_formula.py` and `_refdate.py` were hardened concurrently (strict proposal
validation, timezone-aware dates, `check_formula`, 69 extra tests in
`tests/inference/test_relationship_verification.py`). M3 is built on that version rather than
replacing it.

### Next smallest task

**M4.1, red step:** the stricter-of merge — a rule detector labels an email column
`direct_identifier` while the LLM under-rates it, and the final Sensitivity Class is the stricter of
the two; the LLM may raise risk but never lower it.

## M4 — Privacy analysis

Status: **complete** (M4.1–M4.5, including M4b). 267 tests pass; the Golden Baseline is green.

| Task | File | What it does |
| --- | --- | --- |
| M4.1 Rule detector and Stricter-of Rule | `src/privacy/_analysis.py` | Detects email/phone patterns, unique identifier-shaped values and risky headers; `merge_risk` takes the stricter of the rule label and the LLM label, so the model can raise risk but never lower it. `LabelMap` gives consistent fresh labels and never returns a source label except for exempt labels. |
| M4.2 Policy table (M4b) | `src/privacy/_analysis.py` | `POLICY`: direct identifier → regenerate; quasi → new distribution (0.30); sensitive → fitted (0.12); business metric → trend preserving (0.30); low risk → category resample (0.05); derived → derived. The Noise Budget jitters fitted parameters, never source values. |
| M4.3 Tiny tables (D-11) | `src/generation/_tiny.py` | One Safe Factor per column from [0.60, 1.60] that is never within ±5% of 1.0, plus a ±3% wobble; every cell is verified to end ≥ 5% from its source. |
| M4.4 Distance checks (D-12, D-13) | `src/privacy/_distance.py` | Value Distance (≥5% per cell, aligned on the row key), quasi-identifier row matches, Headline Distance (≥10%), rare combinations only from 20 rows. |
| M4.5 Interfaces | `src/privacy/__init__.py` | `plan_privacy(doc, report, cfg)` and `audit_privacy(doc, data, plan, cfg)`. Derived columns come from HARD formula rules; the Entity Name is exempt; tiny tables are listed; the Privacy Report always carries the two standard limitations. |

Supporting changes: `table_frame` and the cell parsers moved to the Shared Kernel (`src/core/frames.py`,
`src/core/values.py`) so privacy can build the same normalised frames without importing another
module's internals. `src/contracts/dataset.py` adds `CanonicalDataset`; `ChartSpec` and the
Annotator's sensitivity/importance maps were added additively and `SCHEMA_VERSION` is now 2.2.

Tests added (24; 267 in total): `test_analysis.py` (9), `test_distance.py` (6),
`test_interface.py` (5), `test_tiny.py` (4).

Real-data check (LLM off): 60 Privacy Decisions in 0.33 s — 51 `low_risk`, 9 `direct_identifier`,
1 tiny table. The first version exempted a column called "Company Data" from its header alone; that
was wrong (a column of customer names is not the Entity Name), so exemption is now decided by the
values. No column in this PDF is exempt, which is correct.

### Next smallest task

**M5.1, red step:** a Fresh Key test — no generated identifier appears in the source set and keys are
unique — then `src/generation/_keys.py`, followed by categoricals, numerics, dates and applying the
Generation Plan.

## M5 — Basic synthetic generation

Status: **complete** (M5.1–M5.5). 286 tests pass; the Golden Baseline is green.

| Task | File | What it does |
| --- | --- | --- |
| M5.1 Fresh Keys | `src/generation/_keys.py` | New identifiers keep the source's prefix/separator/width but start above the source range, so none equals a source key; numeric keys stay numeric. |
| M5.2 Categoricals | `src/generation/_apply.py` | Laplace-smoothed resampling (never a source label guaranteed; that is the Label Map's job in privacy). |
| M5.3 Numerics | `src/generation/_distributions.py`, `_tiny.py` | Family chosen from the data (skewed positive → lognormal, else normal), clipped to the source range widened by 60%; tiny tables use the Jitter Policy. Every root cell is pushed ≥5% away from its source cell. |
| M5.4 Dates | `src/generation/_apply.py` | Sampled inside the source span; HARD ordering rules are enforced afterwards (`later = earlier + min_gap_days`) so the rule holds on every row. |
| M5.5 Apply the plan + `generate` | `src/generation/__init__.py`, `_apply.py` | Walks the Generation Plan: `sample → fresh_key → copy_fk → derive → rollup → aggregate`, then rounds the Display Tables. `generate(doc, report, plan, cfg, rng)` returns the Canonical Dataset. |

Supporting changes: the `Expr` interpreter moved to the Shared Kernel (`src/core/expr.py`) so
generation can evaluate verified formulas without importing another module's internals; Total Row
knowledge (`total_row_links`, `additive_columns`) moved to `src/core/hierarchy.py` for the same reason.
`InferenceReport.key_columns` records Primary Keys (schema 2.3), which is what lets `generate` mark
Fresh Key steps without re-deriving them.

Tests added (13): `test_keys.py` (3), `test_distributions.py` (4), `test_generate.py` (6: formula holds
on every row, total row equals the sum of its leaves, FKs resolve to generated parent keys, summary
equals the detail aggregate, same seed → identical dataset, display shares the shape).

Real-data check (LLM off): 12 tables generated in 0.54 s, identical across two runs with the same
seed, zero empty cells. No rules exist on this PDF, so every column is a sampled root.

### Next smallest task

**M7.1, red step:** the first validation check — a negative value in a column whose source has no
negatives produces an error-severity Violation that is repairable — then `validation/_checks.py` and
`validation.validate`. (M7 precedes M6 because validation protects the hard gates.)

## M6 — Trends, correlations and breaks

Status: **complete** (M6.1–M6.4, M6b, including the noise gate). 384 tests pass; the Golden Baseline
is green.

| Task | File | What it does |
| --- | --- | --- |
| M6.1 Trend classification | `src/core/stats.py` | Kendall tau + Theil–Sen slope → `up/down/flat/mixed/uncertain`; a rising Trend may contain dips; fewer than five points is `uncertain`. |
| M6.2 Residual correlation (D-15) | `src/core/stats.py` | Spearman rho computed after Theil–Sen detrending, so two unrelated rising series are not called correlated; fewer than 8 pairs is `uncertain`. |
| M6.3 Pattern detection | `src/inference/_patterns.py`, `infer` | Emits `PatternSpec` Trends and Correlations only for tables with a Period or date column, with statistic, p-value, slope and tier. |
| M6.4 Trend generation | `src/generation/_trend.py`, `_apply.py` | Confirmed Trends are generated by `generate_with_shape`: a new start level, a slope within ±40% of the source, up to `max_generation_tries` attempts, every cell ≥5% from its source. An unmet target becomes an `UnmetTarget`, never a failure (D-06). |
| M6b Structural breaks | `src/core/segments.py`, `_patterns.py`, `_trend.py` | 1–3 segments chosen by BIC (2/5/8 parameters), minimum segment length 3, a size test (level jump or slope change ≥ 3× noise), and a permutation test (19 shuffles, p ≤ 0.05). `generate_with_segments` keeps the segment directions and records an `UnmetTarget` if it cannot. |
| M6.4 Correlated generation | `src/generation/_copula.py` | Strong source correlation (|rho| ≥ 0.5) is reproduced by sampling residuals jointly with a Gaussian copula fitted on the source residuals, added to fresh trend lines and re-measured with retries. A weak relationship is never strengthened. |
| Utility gate wiring | `src/validation/_checks.py` | A confirmed Trend whose direction flips is a non-repairable `utility_direction` error (D-18). |

Tests added (23 for M6; 384 in total): `test_stats.py` (7), `test_segments.py` (5, one slow 200-seed
gate), `test_copula.py` (3), `test_trend.py` (4), `test_patterns.py` (4), `test_pattern_gate.py` (2).

One real bug this exposed: **Period labels were treated as Primary Keys.** `FY21A`, `FY22A`, … are
unique and identifier-shaped, so generation replaced the time axis with fresh IDs and the series lost
its order. `key_problem` now rejects a date column and any column whose values all parse as Periods.

### Known limits in M6

- **Seasonality and lagged relationships are not detected.** They need at least two full cycles; no
  detector is written yet, so they are simply absent rather than guessed.
- **Cause Labels are not written.** A Break is detected, but the "why" sentence from nearby text is
  not produced (it would be an LLM proposal allowed only when a Break exists).
- **Patterns need a Period or date column.** `sample_input_1.pdf` keeps its years in the header row
  (transposed tables), so it produces no time series and therefore no patterns.
- The copula accepts a sign match with a strength within ±0.3; beyond that it retries up to
  `max_generation_tries` and records the best attempt as an `UnmetTarget`.







## M7 — Validation and bounded repair

Status: **complete** (M7.1–M7.4). 323 tests pass; the Golden Baseline is green.

| Task | File | What it does |
| --- | --- | --- |
| M7.1 Checks | `src/validation/_checks.py` | Numbers (infinity always; emptiness only when the source had a value), Primary Keys, Foreign Keys, formulas, ordering, Total Rows, aggregates, Value Distance, negative values and utility warnings. Every Violation says whether it is repairable. |
| M7.2 Bounded repair (B-01) | `src/validation/_repair.py` | `run_bounded_repair`: validate → (repair → full validate)* up to `max_repairs`; the verdict comes from the validation after the last repair, so success on the last attempt is accepted. Unrepairable reports stop immediately. Failure raises `HardConstraintError` with the JSON report. |
| M7.3 Repair strategies | `src/validation/_repair.py` | Deterministic order: fill invalid numbers → clip negatives → regenerate keys → re-link foreign keys → move Values away from the source → recompute derived columns → recompute Total Rows → reapply aggregates. Repair only applies rules; it never receives or edits them. |
| M7.4 Utility gate (D-18) | `src/validation/_utility.py` | Error when a confirmed trend flips or a strong correlation changes sign; warning for range, category-share and correlation-strength drift. |

`generate` now runs bounded repair internally with its signature unchanged (`generate(...) -> CanonicalDataset`),
and returns the dataset with the verdict in `dataset.validation`.

Supporting moves (the import-boundary guard test caught the first attempts): distance checks moved to
`src/core/distance.py`, Fresh Keys to `src/core/keys.py`, and `run_bounded_repair`/`repair_dataset` are
part of the validation public interface so generation never imports a private module.

Tests added (16; 323 in total): `test_checks.py` (8), `test_repair.py` (4), `test_utility.py` (4).

Three real bugs found by these tests and fixed:

- **A sampled leaf could become a "Total" row.** Categorical sampling drew from the source labels,
  which include "Total"; the repair pass then linked those random rows as totals and corrupted the
  table. Total Rows are now structural: their label is preserved and leaf rows never sample a total.
- **Value Distance fought the rules.** It pushed Total Rows and aggregate summary cells, so the
  roll-up/aggregate recompute and the distance check oscillated. Derived cells (formula outputs,
  aggregate summaries, Total Rows) are now exempt, and distance repair runs before recomputation.
- **Missingness was treated as a defect.** Empty cells were filled with a median even when the source
  cell was empty. Emptiness is now an error only where the source had a value; mixed text columns are
  no longer coerced into numbers.

Real-data check: generation + validation on `sample_input_1.pdf` takes 0.55 s, is `ok`, needs
**0 repairs**, and reports only 16 utility warnings (category-share drift from the transposed tables).

### Next smallest task

**M8.1, red step:** a Fact computed from the Display Table — the largest segment equals the maximum of
the displayed column — then `generation/_facts.py`, narrative placeholders, charts and HTML.

## M8 - Canonical dataset, narrative, charts and rendering

Status: **complete**. The PDF and Rendered-PDF QA now run against a GTK runtime vendored inside the
project on D:. 456 tests pass; the Golden Baseline is green.

| Task | File | What it does |
| --- | --- | --- |
| M8.1 Display tables | `src/core/rounding.py`, `generation/__init__.py` | Rounded once (half up) after repair, Total Mode per table. Missingness tokens and YoY/QoQ re-computation on the display copy are still not wired (known gap). |
| M8.2 Facts | `src/generation/_facts.py` | Totals, largest value, largest label, share of total, growth and direction words, first/last labels - all read from the **Display Tables** and keyed for placeholders. 150 facts on the real report. |
| M8.3 Narrative | `src/narrative/` | Numbers enter only through placeholders; a digit outside one raises; unknown keys raise; the Section Writer gets fact keys, never values, and its draft is rejected on a stray digit or an invented key; the deterministic template always completes. |
| M8.4 Charts | `src/rendering/_charts.py` | A chart can only be drawn from a registered ChartSpec, read from the Display Table; a JSON sidecar records what was plotted; `assert_chart_matches_table` is the Gate; SVG via matplotlib. |
| M8.5 HTML and PDF | `src/rendering/_html.py`, `__init__.py` | Jinja2 to HTML, then WeasyPrint to PDF with `decraft:synthetic` metadata. WeasyPrint 70 is installed in `.venv` (on D:); its Pango/GTK runtime is vendored at `vendor/gtk` (64 DLLs, 38 MB) and `render` points `WEASYPRINT_DLL_DIRECTORIES`, `FONTCONFIG_PATH` and `PATH` at it, so no system install is needed. |
| M8.6 Rendered-PDF QA | `src/rendering/_qa.py` | Pages rendered to PNG, page count, unresolved placeholders, word count >= 1,000, text-block overflow, chart sidecars and captions; visual similarity is `manual_review_required`. |

Runtime note: the MSYS2 `mingw-w64-x86_64-*` packages installed while trying the system route were
removed again (`pacman -Rdd`), so nothing from this milestone lives on C:. The vendored runtime is git
ignored (`vendor/`); recreate it from any MSYS2 install with `pacman -S mingw-w64-x86_64-pango`, then
copy `mingw64/bin/*.dll` and `mingw64/etc/fonts` into `vendor/gtk`.

Tests added (18; 456 in total): `test_facts.py` (2), `test_placeholders.py` (5), `test_write.py` (3),
`test_charts.py` (3), `test_html.py` (2), `test_qa.py` (3).

Real-data end-to-end (LLM off): 5 sections, **1,083 words**, 150 facts, a **9-page PDF**, and every QA
check green except **page_count**: the source Document Plan says 5 pages (+/-1 allowed). No unresolved
placeholders, no overflow, word count met, chart sidecar equals the table, every chart captioned.
Visual similarity is listed as `manual_review_required`, never passed silently.

Two bugs fixed here: a DataFrame used in a truthiness test in the chart reader, and the placeholder
format spec capturing the space before `}}` (which silently dropped the decimals).

### Next smallest task

**Close the page-count gap or start M9.** Either compact the layout (table font and padding, page
margins) so the output approaches the plan's 5 pages, or record the difference as an accepted
limitation in the Run Manifest. Then M9: `pipeline.run` with the run directory, manifest, stage state,
timeouts, token budgets and the fallback ladder.

## M9 - Pipeline, budgets and fallback

Status: **complete**. 462 tests pass; the Golden Baseline is green.

| Task | File | What it does |
| --- | --- | --- |
| M9.1 `pipeline.run` | `src/pipeline.py`, `scripts/run_decraft.py` | Stages extract -> infer -> privacy plan -> generate -> privacy audit -> write -> render. The run id is derived from the source hash and the Config Hash, so a rerun resumes at the first unfinished stage (`run_state.json`). Every stage has a timeout (`budgets.stage_timeout_seconds`) and runs in a worker that is abandoned on timeout instead of hanging the process. A failed Run still writes its Run Directory and Manifest. |
| Run artifacts | `src/pipeline.py` | `manifest.json`, `config.json`, `inference.json`, `privacy_plan.json`, `privacy_report.json`, `validation_report.json`, `utility_report.json`, `rendered_pdf_qa.json`, `facts.json`, `charts.json`, `lineage.json`, `narrative.json`, `canonical_dataset/*.csv`, `display/*.csv`, `render/index.html`, `render/output.pdf`, `render/pages/*.png`, `render/contact_sheet.png`, `logs.jsonl`. All output hashes go into the Manifest. |
| M9.2 Fallback ladder | `src/pipeline.py`, `src/llm` | LLM off -> deterministic templates, logged as `fallback`; malformed replies -> `None` -> template; a hung client is cut off by the gateway timeout; prompt injection changes nothing because facts never enter a prompt and extracted text is untrusted data. |
| CLI | `scripts/run_decraft.py` | `python scripts/run_decraft.py [pdf] [--llm] [--artifacts DIR]`. |

Tests added (6; 462 in total): `tests/pipeline/test_run.py` - a full run writes the directory, Manifest
and PDF; a rerun resumes instead of repeating `infer`; malformed LLM replies fall back without an
exception; a slow LLM times out without hanging; a missing source still writes a `failed` Manifest;
prompt injection in the text is inert with the LLM off.

Real run (LLM off): run id `7799d9f6e42f-b1098e25`, **51 hashed outputs**, gates
`validation True, word_count True, no_placeholders True, chart_table True, privacy_audit True,
rendered_pdf_qa False` (9 pages against the plan's 5). Status: **failed**, because of that one gate.
The HTML, PDF, charts and every report are present.

A real bug this exposed: two SOTP columns (table_006) had generated totals within 10% of the source
totals, failing Headline Distance (D-13). Generation now scales a root column's leaves 12% away from
the source total when the total lands inside the headline band; both violations disappeared.

Known gaps: the `generate` stage always reruns (the resume covers `extract`, `infer` and
`privacy_plan`), and the rendered page count still exceeds the source plan.

### Next smallest task

**M10:** a reproducibility test (two runs with the same seed and cached replies produce identical
CSV hashes), inference scoring on fixtures, and the measured coverage report. Alternatively close the
page-count gap by compacting the layout, which is the only failing gate on the real report.

## M10 - Audit, reproducibility and evaluation

Status: **complete**. 500 tests pass; the Golden Baseline is green; coverage of the new packages is
**87%** (gate: 80%).

| Task | File | What it does |
| --- | --- | --- |
| M10.1 Run directory | `src/pipeline.py` | One Run writes its Manifest, Config snapshot, Inference Report, Privacy Plan and Report, Validation Report, Utility Report, Rendered-PDF QA, Facts, Charts, Lineage, Narrative, full-precision and display CSVs, HTML, PDF, page PNGs, contact sheet and `logs.jsonl`; every artifact is hashed into the Manifest. The real Run writes **67 hashed outputs**. |
| M10.2 Reproducibility | `tests/pipeline/test_reproducibility.py` | Two cold Runs with the same seed and Config produce identical `canonical_dataset/*.csv` hashes and the same `run_id`. (The Config Hash includes `artifacts_dir`, so the test uses one directory and deletes it between Runs instead of comparing two locations.) |
| M10.3 Inference scoring | `tests/inference/test_scoring.py`, `tests/fixtures/` | Four Fixtures - retail, finance, energy and the unknown domain - each with `source.csv` and a hand-written `expected_inference.json`. The test reports semantic-type accuracy, checks expected rules are enforced with the expected tier, and **asserts zero falsely enforced rules** (the gate). |
| M10.4 Coverage | measured | `pytest --cov=src/contracts --cov=src/core --cov=src/inference --cov=src/privacy --cov=src/generation --cov=src/validation --cov=src/narrative --cov=src/rendering --cov=src/llm --cov=src/pipeline` -> **87%** (5458 statements, 720 missed). `src/blueprint` and `src/planner` are reported without a gate, as D-10 requires. |

Real Run on `sample_input_1.pdf` (LLM off): **status `ok`**, every gate true -
`validation`, `rendered_pdf_qa`, `word_count`, `no_placeholders`, `chart_table`, `privacy_audit`.

Bugs fixed while measuring (all surfaced by the real document):

- **Headline Distance for a column maximum (D-13).** The `largest` fact could sit within 10% of the
  source maximum even when every cell was 5% away. Generation now moves the argmax cell at least 12%
  from the source maximum (and then re-checks the total), skipping statement tables whose row shapes
  must survive.
- **Displayed Value Distance for scaled statements.** A statement in "Rs cr" can print two very
  different base values as the same number (1.6 and 2.0 crore both show 2). Generation now moves one
  whole display unit until the printed values differ by at least 5%.
- **Statement totals were too close to their source.** The per-table factor for statements now
  excludes +/-12% instead of +/-5%, so the row "latest" facts also clear Headline Distance, while the
  row trends stay intact (the user's statement-trend tests pass for 8 seeds).
- **Manifest keys are POSIX paths**, so hashes compare across platforms.

Known gaps: the `generate` stage always reruns on a resume (`extract`, `infer` and `privacy_plan`
resume); seasonality and lagged relationships are not detected; inference scoring uses a deterministic
FakeLLM reply per fixture rather than a live model.

### Next smallest task

**M11 - acceptance.** Run the pipeline on `sample_input_1.pdf` plus at least two PDFs from other
domains and the unknown-domain fixture, record every gate in the Manifest, and write the M11 report
with the remaining limitations (seasonality, live-model scoring, `generate` resume).

## v3.1 addendum - M8.0 foundations (in progress)

The v3.1 addendum replaces v3 milestones M8-M11. It was read, and the new
`UBIQUITOUS_LANGUAGE.md` (with the section 12 v3.1 terms) is now the repository copy.

Done in this slice:

| Item | File | Notes |
| --- | --- | --- |
| M8.0.1 Config | `src/config.py`, `config/default.yaml` | `Providers` (text/vision/image, each with `ProviderLimits`), `ReportSettings` (period shift, word/row tolerances, critic rounds, retries, dpi, image size). YAML and code defaults agree (`test_v31_defaults`). |
| M8.0.2 Rate limiter | `src/llm/_limiter.py` | Client-side requests/min, tokens/min, requests/day, tokens/day; waits instead of erroring; the daily quota raises `LLMBudgetExceeded`. |
| M8.0.2 Payload fitting | `src/llm/_payload.py` | `estimate_tokens`, `compact`, `fit_payload` (drops optional fields in priority order), `batch_items`. |
| v3.1 contracts | `src/contracts/report.py` | `StyleProfile`, `TextStyle`, `FontSpec`, `PageTemplate`, `Block`, `FigureInfo`, `RecoveredChart`, `Outline`, `StoryBible`, `Claim`, `Conflict`, `QAReport`, `StyleAdjustments`, ... (additive). |
| Story Bible and claims | `src/narrative/_claims.py`, `_tokens.py`, `src/rendering/_fit.py` | `merge_claims`, `check_resolution`, Period Tokens (`illegal_digits`), text fitting (`fits`, `word_budget`). |
| Prompt specs | `src/narrative/_prompts.py`, `src/style/_prompts.py`, `src/rendering/_prompts.py` | Versioned prompts with per-prompt input/output caps, temperature, role and rationale (E-05, E-23). `PromptSpec` gained those fields with defaults. |
| Period shift (E-16) | `src/core/periods.py` | `shift_period` and `format_like` (FY24 -> FY25, Q3 2023 -> Q3 2024). |
| Style interface | `src/style/__init__.py` | `extract_style`, `build_templates`, `analyse_figures` signatures, raising `NotImplementedError` (v3 section 2.3). |

Tests: the Appendix S test files were brought in unchanged and pass
(`test_limiter_payload.py`, `test_report_contracts.py`, `test_claims_tokens_fit.py`,
`test_prompt_budgets.py`), plus `test_v31_defaults`. The full suite is green (exit 0).

Still to do for M8.0: gateway v2 (image payloads, `cache_only`/`CacheMiss`, JSONL prompt log,
input caps), the Groq/Cloudflare clients and `make_gateways`, the prompt-log script, and the stage
runner. Then M8.1 (style extraction body), M8.2-M8.12.

## v3.2 addendum - originality foundations (task 1)

The v3.2 addendum replaces the v3.1 report-generation plan and adds the challenge-text overrides
E-25 to E-30. The new `UBIQUITOUS_LANGUAGE.md` (with Tone Profile, Avoid List, Generic Label, Label
Fabricator and Font Lock) is now the repository copy.

Done in this task:

| Item | File | Notes |
| --- | --- | --- |
| Originality guards (E-25, E-26, E-27, E-28) | `src/narrative/_originality.py`, `tests/narrative/test_originality.py` | `source_proper_nouns`, `reused_names`, `is_generic_label`, `check_label_map`, `tone_profile`; the test also fails if any prompt mentions a fixed document type ("corporate", "annual report"). |
| v3.2 prompt specs | `src/narrative/_prompts.py`, `src/style/_prompts.py`, `src/rendering/_prompts.py` | Refreshed from Appendix S.6: document-type-free wording, measured tone, Font Lock, plus the `label_fabricator` prompt. Budget tests still pass. |
| v3.2 contracts | `src/contracts/report.py` | Refreshed additively (tone/avoid/font-lock fields). |
| Config (section 7) | `src/config.py`, `config/default.yaml` | `row_compress_max` renamed to `row_padding_compress_max` (v3.2); provider and report settings already in place from the v3.1 slice. |

Checks: `pytest -q` green (exit 0), including the new originality tests and the prompt-budget tests.

Next: M8.4.0 label fabrication and the avoid list in `prepare_for_report`, then the remaining
M8.0 gateway v2 work and the style module.

## v3.2 addendum - M8.0.3 gateway v2 and providers (task 2)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Gateway v2 | `src/llm/_gateway.py` | Per-prompt budgets (`max_input_tokens`, `max_output_tokens`, `temperature`, `reasoning_effort`, `role`), `images=` on `ask_json`, input cap enforced before sending, `calls limits` via an injected `RateLimiter`, `cache_only` with `CacheMiss`, JSONL `log_path`, and `start_stage()` so every Pipeline Stage gets its own token budget. An unreachable model server (`LLMUnavailable`) is logged and falls back instead of raising. |
| Providers (E-01) | `src/llm/_providers.py` | `GroqClient` (JSON mode, `reasoning_effort` with one retry on a 400), `CloudflareVisionClient` (images as `data:image/jpeg;base64,` URLs), `CloudflareImageClient` (FLUX, seed and steps). Keys only from `GROQ_API_KEY`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`. |
| Factory (E-04, E-06, E-24) | `src/llm/__init__.py` | `make_gateways(cfg)` returns `{"text", "vision", "image"}`, one limiter per provider from `providers.*.limits`, caches under `artifacts/llm_cache/<kind>/`, prompt log at `artifacts/prompt_log.jsonl`, and `DECRAFT_CACHE_ONLY=1` support. A missing key disables that gateway instead of failing the Run. |
| Client compatibility | `src/llm/_clients.py` | The Ollama client accepts `images` and `reasoning_effort`, so the legacy local path still works through the v2 gateway. |
| Live smoke script | `scripts/smoke_llm.py` | One tiny text call, one vision call with a 256 px JPEG and one image call; prints the model ids or why a provider was skipped. |
| Tests | `tests/llm/test_gateway.py`, `test_providers.py`, `test_gateways_factory.py` | 12 gateway tests (including cache-only, input cap, prompt log), 5 provider request-shape tests with `httpx.MockTransport` (model, `response_format`, `reasoning_effort` retry, data URL, seed/steps), 2 factory tests. `tests/llm` is 30 tests; the full suite is green. |

Not run: `python scripts/smoke_llm.py` - there are no provider keys on this machine, so "all three
providers answer" (the M8.0.3 Done condition) remains open. It must be run where `GROQ_API_KEY`,
`CF_ACCOUNT_ID` and `CF_API_TOKEN` are set, and the verified model ids and limits then written into
`config/default.yaml` and the M8 report.

Next: M8.0.4 stage runner (`scripts/stage.py`), then M8.1 style extraction.

## v3.2 addendum - M8.0.4 stage runner (task 3)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Stage machine | `src/stage_runner.py` | `Stage(name, run)`, the v3.2 stage order (`style, templates, figures, prepare, charts, plan, write, fit, images, render, qa`), and `run_stages(stages, until, root, fresh)`. A stage writes `output.json` and `done.json` (a sha256 of its inputs); a rerun reuses it when the hash matches, `--fresh` ignores the state. Returns and prints one JSON summary. |
| CLI | `scripts/stage.py` | `--pdf`, `--until`, `--dir` (default `artifacts/runs/dev`), `--fresh`. `style` and `templates` are wired lazily; every later stage stops with a `NotImplementedError` naming its milestone rather than pretending to run. |
| Tests | `tests/pipeline/test_stage_runner.py` | 5 tests: exactly the stages up to the target, reuse on the second run (no stage called), `--fresh` re-runs all, JSON summary printed, unknown stage rejected. |

Check: `python scripts/stage.py --until style` stops with
`NotImplementedError: stage 'style' lands in M8.1` (expected until M8.1 is built), and `pytest -q`
is green (exit 0).

Next: M8.1 style extraction (`style.extract_style`).

## v3.2 addendum - M8.1 style extraction (task 4)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Fonts and Text Styles | `src/style/_extract.py` | Spans from `page.get_text("dict")`; subset prefixes and style suffixes are cleaned with a family alias table (`ABCDEF+Calibri-Bold` -> `Calibri`, bold); styles cluster by family, bold, italic, size (0.5 pt) and colour, with line height = median baseline gap / size. The style list is capped at 25 by character count (the long tail of odd colours is noise). |
| Embedded fonts (E-21) | `src/style/_extract.py` | `doc.extract_font` for TrueType/OpenType/CFF files, accepted only when fontTools shows more than 200 glyphs; complete files are written to `artifacts/runs/dev/style/fonts/`. Every other font gets the metric-compatible fallback (Calibri -> Carlito, Arial/Helvetica -> Liberation Sans, Times -> Liberation Serif, ...; unknown faces by serif/sans hint). |
| Palette and geometry | `src/style/_extract.py` | Palette from span colours plus drawing fills/strokes, weighted by characters and area; near-white always dropped, near-black only when the body text is black; top 8. Page size, margins (top/right/bottom/left from the text bbox), median table stroke width and the running header/footer (text repeated at the same top/bottom position on 60% of pages, with the page-1 top line as the header fallback). |
| Public interface | `src/style/__init__.py` | `extract_style` now returns a validated `StyleProfile`; `build_templates` and `analyse_figures` still raise `NotImplementedError` for M8.2/M8.3. |
| Tests | `tests/style/test_extract_style.py` | Font-name cleaning and aliases, fallback mapping, a synthetic two-page PDF (18 pt bold title, 10 pt body, blue rectangle, running header/footer) giving two styles, `#1F3864` first in the palette and non-zero margins, and the real sample giving 3-25 styles, a 3-8 colour palette, a header, a footer and non-zero margins. |

Checks: `pytest tests/style -q` -> 4 passed; `python scripts/stage.py --until style` prints the
profile (styles, palette, page size, margins, header/footer) and a second run reports
`"executed": []` (it reuses the saved output); `pytest -q` is green (exit 0).

Real sample: 25 styles after the cap (35 raw colour/size variants), palette of 8, margins
(70.92, 19.35, 65.56, 22.44) pt, header "11 th September 2023 Q1FY24 RESULTS UPDATE", footer
"www.geojit.com", table border 0.75 pt, 8 fonts with fallbacks.

Next: M8.2 page templates (`build_templates`: Block classification, mirror/flow, section ownership).

## v3.2 addendum - M8.2 page templates (task 5)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Block classification | `src/style/_templates.py` | Text blocks become heading / header / footer / page_number / caption / text: page numbers are digits in the top or bottom 8%; the running header/footer match the extracted text; headings need a style at least 1.2x the body size, or bold text at body size with at most 12 words; captions are smaller text within 24 pt of a figure. Tables are detected as line grids from `get_drawings` (at least 2 horizontal and 2 vertical segments forming a region under 60% of the page); other drawings are decoration. Images are `photo`, or `logo` when the xref repeats or the first image on page 1 is at most 25% of the page wide. Text inside a table/photo is dropped. Every block carries `block_id` (`p<page>b<nn>`), `source_words` and `section_id`. |
| Mirror or flow (E-15) | same | `flow` for a rotated page, more than three real text columns (x0 clusters whose blocks sit side by side, rather than any indentation), or overlapping text blocks (IoU > 0.1); otherwise `mirror`, with the reason recorded. |
| Section ownership | same | A heading opens a section (S1, S2, ... in document order); text before the first heading is `S0`. Every block has a section id. |
| Public interface | `src/style/__init__.py` | `build_templates` is exported; `analyse_figures` still raises `NotImplementedError` for M8.3. |
| Tests | `tests/style/test_build_templates.py` | 6 tests: the exact kind sequence on a synthetic page (heading, text, text, table, caption, page_number); a rotated page flows with reason `rotated`; four real columns flow with `columns`; the overlap helper; front matter S0 then S1/S2 by headings; the real sample gives 5 templates, unique block ids and every block owned. |

Checks: `pytest tests/style -q` -> 10 passed; `python scripts/stage.py --until templates` prints the
templates and reuses them on the next run. Real sample: 5 pages (page 1 flow `columns`, pages 2-5
mirror), 176 blocks, 44 sections, 3,539 words in text/heading/caption blocks against the document
plan's 3,565 (0.7% difference).

Not done yet: the debug overlay PNGs (`overlay_p<N>.png`) named in the M8.2.1 check.

Next: M8.3 figure analysis (`analyse_figures`, Figure Analyst and recovered chart data).

## v3.2 addendum - M8.3 figure analysis (task 6)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Figure Analyst (E-03) | `src/style/_figures.py` | One vision call per page with figure blocks: the page as a JPEG (<= 1024 px, quality 80) plus `[{"id","bbox"}]`. The reply is validated strictly (known kinds, every id at most once); missing ids fall back to the rules (logo, photo, or chart with `chart_kind="bar"`) and log a warning. Without a gateway every figure gets a rule label and no call is made. |
| Lone charts and recovery (E-19) | same | A chart is lone when its section holds no table block. Its bbox is cropped at 150 dpi and sent to `chart_data_recovery`; the reply is accepted only when the figure id matches, categories are non-empty, every series has one value per category, and confidence is at least 0.4. Accepted data becomes a `RecoveredChart`; rejected charts are logged for QA. `recovered_to_source_table` builds the hidden `rc_<figure_id>` Source Table with the categories as the first column (marking `display=False` belongs to M8.4). |
| Public interface | `src/style/__init__.py` | `analyse_figures` is exported. |
| Stage runner | `scripts/stage.py` | The `figures` stage is wired and `--llm` enables the provider gateways. Its summary has `charts` (kind counts), `photos`, `logos`, `figures`, `recovered` and `llm_calls`. |
| Tests | `tests/style/test_figures.py` | 6 tests: a valid reply labels a chart and a valid recovery converts to a Source Table; a missing id falls back with a warning; no gateway gives rule labels; series lengths must match; a confidence below 0.4 is rejected; the real sample gets rule labels without calls. |

Checks: `pytest tests/style -q` -> 16 passed; `python scripts/stage.py --until figures` prints
`{"figures": 11, "photos": 3, "logos": 8, "charts": {}, "llm_calls": 0}` on the sample without keys
(the rule path); `pytest -q` is green (exit 0).

Note: page templates do not emit a `chart` block kind yet (vector charts are drawings), so charts can
only come from the vision labels; recovered tables are generated in M8.4.

Next: M8.4 `prepare_for_report` - fabricated labels and the avoid list (E-27, E-28), period shift,
row counts, recovered tables and facts with bands.

## v3.2 addendum - M8.4 data preparation (task 7)

Done:

| Item | File | Notes |
| --- | --- | --- |
| `prepare_for_report` | `src/generation/_prepare.py`, `src/generation/__init__.py` | The new public function (generate is unchanged): shifts periods, fabricates labels, enforces row counts, adds recovered-chart tables, rebuilds Display Tables, computes facts with bands/owners, re-runs validation and raises `HardConstraintError` on failure. |
| Period shift (E-16) | same | Period column headers and period-column values move forward by `period_shift_years` (FY24 -> FY25); the matching Rule columns, formula trees, params, lineage keys and key columns are renamed with them, and the Source Document is shifted too so validation still aligns. `allowed_periods` carries both spellings (FY25 and FY2025). |
| Fabricated labels (E-27/E-28) | same, `src/generation/_prompts.py` | Category labels that still occur in the source text are replaced consistently across every table; generic vocabulary (months, regions, standard line items) stays. `label_fabricator` (v1) is used when a gateway is enabled and its reply passes `check_label_map` + `reused_names`; otherwise a seeded deterministic mapping is used. `avoid.json` and `labels.json` are written under `artifacts/runs/dev/prepare/`. |
| Row counts (E-17) | same | Leaf rows outside the source +/-20% band are resampled to a target in the band, Hard formulas are recomputed and additive columns rolled up. Value Distance is skipped for tables whose row count changed (row identity is no longer meaningful). |
| Recovered tables (E-19) | same | Each Recovered Chart becomes a hidden `rc_*` Source Table, which goes through inference, privacy and generation; its Chart Spec is registered with `display=False`. |
| Display and facts | same | Display Tables are rebuilt from the prepared data; every Fact gets a band (`strong increase` ... `sharp decline`; `dominant`/`major`/`minor`) and exactly one owner. |
| Contracts | `src/contracts/__init__.py`, `dataset.py` | Additive: `ChartSpec.display`, `Fact.band`, `Fact.owner`, `CanonicalDataset.allowed_periods`. |
| Shared helpers | `src/core/recovered.py` | `recovered_to_source_table` moved to the Shared Kernel (the guard test rejects cross-module private imports); originality helpers stay in `src/core/originality.py` (the user's implementation, with `name_words`/`names_in`). |
| Stage runner | `scripts/stage.py` | The `prepare` stage loads the latest pipeline Run (inference, CSVs, facts, charts, lineage, validation), applies preparation and saves the prepared dataset, display and facts under `artifacts/runs/dev/prepare/`. |
| Tests | `tests/generation/test_prepare.py` | 6 tests: period shift + allowed forms, source-label replacement and generic retention + avoid file, row-count band + totals, hidden recovered table + `display=False`, fact bands/owners + validation ok, band thresholds. |

Checks: `pytest tests/generation/test_prepare.py -q` -> 6 passed; full suite green (exit 0).
`python scripts/stage.py --until prepare` on the sample prints: shift 1, 25 labels (all replaced),
avoid list 262 names, 277 facts, bands `{sharp decline: 14, decline: 5, flat: 4, increase: 6,
strong increase: 40, minor: 2}`, and all 12 tables within +/-20% of their source row counts.

Note: run a fresh `scripts/run_decraft.py` before the prepare stage; an older Run directory can hold
stale CSVs (one had an all-empty date column) and the stage validates its input.

Next: M8.5 charts for every table (`choose_chart_kind` + sized SVG charts and sidecars).

## v3.2 addendum - M8.5 charts for every table (task 8)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Chart kind (E-19) | `src/generation/_charts.py` | `choose_chart_kind`: the source chart kind wins when the Figure Analyst supplied one; otherwise a single share-like column summing to 100 with at most 8 rows is a donut, more than 6 period columns is a line (2-6 periods a bar), categories with one to three measures are a horizontal bar. |
| One chart per table | `src/generation/_charts.py` | `register_all_charts` keeps the Document Plan's pairs and adds a spec for every remaining displayed table (label column, up to three measures, unit from the Field Profile, a readable title, data labels for small tables). Hidden tables (`display=False`) are never charted twice. |
| SVG rendering | `src/rendering/_charts.py` | Horizontal bars, donuts, bar, line and pie; data labels when the source printed values; the unit goes on the axis; title, legend toggle, palette order and the Font Lock size are respected. `draw_png` writes a raster copy for the contact sheet, `contact_sheet` tiles them. |
| Contracts | `src/contracts/__init__.py` | `ChartSpec.kind` widened with `horizontal_bar`/`donut`; added `unit`, `has_data_labels`, `has_legend` (all additive). |
| Stage runner | `scripts/stage.py` | The `charts` stage loads the prepared dataset, registers every chart, writes SVG + PNG + sidecar for each, builds `contact_charts.png` and reports `{charts, kinds, sidecars_match, every_displayed_table, unchartable, contact_sheet}`. |
| Tests | `tests/generation/test_chart_kind.py`, `tests/rendering/test_charts_all.py` | 8 tests: period-count rule, share-donut, category horizontal bar, source kind wins, one chart per table, SVG text contains the labels, sidecar equals the display, PNG and contact sheet, donut/horizontal-bar rendering. |

Checks: `pytest tests/generation/test_chart_kind.py tests/rendering/test_charts_all.py -q` -> 8 passed;
full suite green (exit 0). `python scripts/stage.py --until charts` on the sample prints
`{"charts": 8, "kinds": {"bar": 2, "line": 6}, "sidecars_match": true,
"every_displayed_table": true}` and writes `artifacts/runs/dev/charts/contact_charts.png`. Four tables
(`table_001`, `table_002`, `table_005`, `table_012`) hold no numeric measure (codes, labels) and are
listed as `unchartable` instead of being given an invented chart.

Next: M8.6 planning (`narrative.plan`: outline skeleton, Titles call, Story Bible call).

## v3.2 addendum - M8.6 planning (task 9)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Outline skeleton (M8.6.1) | `src/narrative/_plan.py` | Sections come from the Page Templates in reading order; a section's source title is its heading text; writable block ids and fact keys are assigned so every text block and every fact belongs to exactly one SectionPlan (front matter is S0, unowned facts go to the first content section). |
| Titles call (M8.6.2) | same | `outline_titles` v2 payload: company, `secs` (id, source title, role, word budget) and the top 30 fact keys. Validation: same ids in the same order, title at most 12 words, and no shared run of 3 non-generic words with the source title. A rejected reply falls back to a deterministic title list ("Year in Review", ...) and a goal sentence, flagged by being the fallback. |
| Story Bible (M8.6.3) | same | `story_bible` v2 payload: company, outline titles/goals, allowed periods, avoid list. Validation: 8-20 entities, unique ids and names, known kinds, digits only as allowed Period Tokens, no reused source names. One retry carries the error list in the payload; otherwise a deterministic 9-entity bible (role, 3 initiatives, 2 products, place, org unit, partner) with an allowed period year. |
| Block text | `src/contracts/report.py`, `src/style/_templates.py` | `Block.text` added (additive) and filled from the source text, so planning (and later writers) can use the paragraph and heading wording. |
| Public interface | `src/narrative/__init__.py` | `plan(data, templates, doc, cfg, gateway) -> (Outline, StoryBible)` exported next to `write`. |
| Stage runner | `scripts/stage.py` | The `plan` stage loads the prepared dataset, facts, templates and Source Document, calls `plan`, and prints report title, sections (source title, new title, goal, block/fact counts) and the bible summary (entity count by kind, dates). `--llm` enables the text gateway. |
| Tests | `tests/narrative/test_plan.py` | 7 tests: skeleton ownership round-trip; titles applied in order; reordered ids and copied titles rejected; title rules; bible retry carries the FY27 error then accepts a valid reply; 21 entities fall back; validation reports duplicates and bad years. |

Checks: `pytest tests/narrative/test_plan.py -q` -> 7 passed; full suite green (exit 0).
`python scripts/stage.py --until plan` on the sample gives 44 sections (the report is dense with
headings), real source titles, fallback titles, and a 9-entity bible with `Q4FY24` as its only date
(allowed). Run `--fresh` after the Block text change: the old cached templates carried no text.

Next: M8.7 writing (`narrative.write`: writer payloads, parallel calls, section checks).

## v3.2 addendum - M8.7 writing (task 10)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Writer payload (M8.7.1) | `src/narrative/_writer.py` | `build_writer_payload` sends the required `sid,t,g,bl,f,per` and adds `bi`, `ol`, `else` through `fit_payload` in that priority order, so a tight cap drops `else` first and `bi` last. Block word ranges come from the source block's word count (+/-15%). |
| Section checks (M8.7.3) | same | `check_section` verifies block ids, word ranges (with 5% slack), digits outside placeholders/Period Tokens, unknown placeholder keys, unclosed placeholders, claims that reference an unknown entity, and a run of `ngram_overlap_max` (8) words shared with the source text. |
| Parallel writers (M8.7.2) | same | `write_blocks` runs one writer per section under a `ThreadPoolExecutor` sized by `providers.text.max_concurrency` (the gateway's limiter still throttles), retries with the error list up to `report.writer_retries`, and otherwise uses deterministic template text, truncated to the section's source word budget. Fallback flags are recorded only when a model was used. |
| Contracts | `src/contracts/__init__.py` | `NarrativeDoc.blocks` and `NarrativeDoc.flags` added (additive). |
| Stage runner | `scripts/stage.py`, `src/stage_runner.py` | The `write` stage runs the block-level path; `--dry-run` prints each section's payload token estimate against the cap. The cache key now includes the mode, so a dry-run does not mask a real write. |
| Tests | `tests/narrative/test_write_blocks.py` | 5 tests: payload fits the cap and drops `else` first; clean reply passes and each error type is reported; retry with the error list then success; three failures fall back and flag; the LLM-off path writes deterministic text. |

Checks: `pytest tests/narrative/test_write_blocks.py -q` -> 5 passed; full suite green (exit 0).
`python scripts/stage.py --until write --dry-run` on the sample: 44 sections, cap **2,768** tokens,
**43** sections below the cap, **1** above it (8,803 tokens) and therefore needing a block split;
`--until write` (LLM off) writes **3,970 words** with **44/44** sections on the deterministic path
and no flags.

Known gaps: the block-level path is not yet called by `pipeline.run` (it still uses the section-level
`narrative.write`, which the M8.11 renderer will replace); claims merge and the Reconciler are M8.8;
one very large section must be split into several writer calls.

Next: M8.8 consistency (claims merge and the Reconciler with its four guards).

## v3.2 addendum - M8.8 consistency (task 11)

Done:

| Item | File | Notes |
| --- | --- | --- |
| New ids (M8.8.1) | `src/narrative/_consistency.py` | `rename_new` turns a writer's `N1` into `N:<section>:1`, so parallel sections never collide. `merge` converts the `StoryBible` to the S.3 `Entity` map and runs `merge_claims`, returning the merged bible, the conflicts and the claims whose entity is unknown. |
| Four guards (M8.8.2) | same | `rewrite_guards`: (a) the choice is one of the candidates (`check_resolution`); (b) the rewrite may only touch a block that holds a flagged paragraph, and a paragraph diff rejects any change to an unflagged paragraph (or a change in paragraph count); (c) the rewritten text must pass the writer's digit and placeholder checks and contain the chosen value verbatim; (d) a capitalised multi-word name that is not in the bible, the source glossary or the source text is rejected. |
| Reconciler loop | same | `reconcile` runs at most `report.reconcile_rounds` (2) rounds; a rejected reply stays in the queue for the next round; the accepted rewrite replaces the block text and updates the bible entity (`name` or the attr). What is still conflicting after the last round has its sentences dropped and is counted in the flags. |
| Writer integration | `src/narrative/_writer.py` | Every successful section returns its resolved block texts plus `Claim`s (carrying the block id) and renamed new entities. After the parallel pass: merge, drop the sentences of unknown claims, reconcile conflicts, rebuild each section's text from its blocks, and record the statistics. |
| Contracts | `src/contracts/__init__.py` | `NarrativeDoc.claims`, `new_entities`, `conflicts`, `resolved`, `dropped`, `unknown_refs` (additive). |
| Stage summary | `scripts/stage.py` | `--until write` now prints `claims`, `new_entities`, `conflicts`, `unknown_refs`, `resolved`, `dropped` and `final_conflicts` beside the per-section rows. |
| Tests | `tests/narrative/test_consistency.py`, `tests/narrative/test_write_blocks.py` | 19 tests in the two files: namespaced ids; conflict and unknown-ref detection; invented choice, unflagged block, unflagged paragraph and missing-value rewrites rejected; a valid rewrite applied with the bible updated; unresolved conflicts dropped; an accepted single-paragraph diff; claims merged by the writer; two writers disagreeing reconciled end to end. |

Checks: `pytest tests/narrative/test_consistency.py tests/narrative/test_write_blocks.py -q` -> 19 passed;
the full suite is green. `--until write` on the sample reports the new counters (0 claims / 0 conflicts
without a model, 44/44 sections) and keeps the per-section `ok` rows.

Known gaps: claim merging and reconciliation are only exercised with a model in the tests here (no
provider keys on this machine); the reconciled bible is applied within the rounds and is persisted when
the pipeline owns the plan stage (M8.11).

Next: M8.9 box fitting (per-block word budgets, shorten/fit inside each box).

## v3.2 addendum - M8.9 box fitting (task 12)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Text measurement (M8.9.1) | `src/rendering/_measure.py` | `advance_width_fn` reads the font's `hmtx` advances and `unitsPerEm`, ignores kerning and applies the 2% safety margin. A block's font is the extracted file when it exists, else the metric-compatible fallback family, else DejaVu Sans from the matplotlib data (always on D:). `box_for` / `width_fn_for` take the width, height, size and line height from the block's bbox and Text Style. |
| Shorten loop (M8.9.2) | `src/rendering/_fit_doc.py` | `fit_blocks` measures the resolved text; a block that does not fit goes to `shorten` with `max_words = floor(words x have / need x 0.95)`, at most 2 tries. A draft is accepted only if it keeps every original placeholder, keeps every original capitalised name, adds no digits and fits. Otherwise the text is cut at the last full sentence that fits and flagged; a final word-level trim guarantees the box is not left overflowing. Font sizes never change. |
| Writer | `src/narrative/_writer.py` | The deterministic fallback is now spread over the section's blocks (`_split_for_blocks`) by source word budget, so every mirrored box has text to fit; `NarrativeDoc.blocks` is populated. |
| Stage | `scripts/stage.py` | `--until fit` measures every block and prints one row `{id, need, have, fits}` plus `{overflow_before, shortened, cut}`; the write stage now saves the block texts and each section's text. Every block fits after the stage in the sample run. |
| Tests | `tests/rendering/test_measure.py`, `tests/rendering/test_fit_doc.py` | 7 tests: "MMMM" wider than "iiii" from real hmtx, box/text-style mapping, already-fitting text untouched, a valid shorter text accepted, a shorter draft that adds a placeholder rejected, two failed drafts cut at the last full sentence with a flag, and the LLM-off cut path. |

Checks: `pytest tests/rendering/test_measure.py tests/rendering/test_fit_doc.py tests/test_guards.py -q`
-> 10 passed; the full suite is green. On the sample `--until fit`: **153 blocks**, **111** overflowed
before, **0** shortened (no model), **111** cut, **0** blocks still overflowing, **1,446 words**.

Known gaps: with a model the `shorten` path reduces the cut count; the deterministic sample is the
worst case. The final `write()` gate ("every block fits") is satisfied by the write+fit pair; the
pipeline switches to this pair in M8.11.

Next: M8.10 images (photo slots, captions, FLUX prompts).

## v3.2 addendum - M8.10 images (task 13)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Photo slots (M8.10.1) | `src/rendering/_images.py` | `photo_slots` turns every `photo` block into a slot and finds the nearest `caption` block above or below it (within 48 pt); the caption is the writer's text for that block. `logo_slots` lists the logo blocks. |
| Prompt, generation, check (M8.10.2) | same | `build_prompt` uses the exact spec shape (caption, composition from the Figure Analyst, the top three palette hexes, document type, natural light, no text/logos/watermarks). `image_size` keeps the slot's aspect ratio with the longest side <= 1024 and both sides multiples of 8. Seeds are `cfg.seed + slot_index + 100*k` for the initial call and up to `report.image_retries` regenerations. `image_check` is the existing rendering prompt (budget aligned to the v3.2 table: 1600 in / 300 out), so there is no duplicate prompt name. |
| Caption simplification | same | After three misses the caption goes through a `shorten` call with the `missing` list; sentences that still mention a missing element are removed, and the result is flagged. |
| Logos | same | `extract_logo` copies the original embedded image at the logo's place (else a 300 dpi crop). Logos are written to `images/logo_<i>.png` and never passed to the image or vision client. |
| Saves and summary | same | Generated photos are JPEG quality 80, longest side 1024; the summary is `{photos, slots, generated, checks_passed, captions_simplified, skipped, logos}`. A photo with neither caption nor description is skipped instead of inventing content. |
| Provider | `src/llm/_providers.py` | `CloudflareImageClient.generate` accepts the slot's `width`/`height` (longest side 1024) and keeps recording the seed. |
| Stage | `scripts/stage.py` | `--until images` lists slots and captions, copies logos, and records the simplified captions in the output's block map for rendering. |
| Tests | `tests/rendering/test_images.py` | 8 tests: a first-try match generates once; two misses then a match generates three times with seeds 42/142/242; three misses simplify and flag; a simplified caption never mentions the missing elements; a caption block below a photo is its caption; the prompt and size match the spec; logos are copied and never sent to the client; a photo with no caption/description is skipped. |

Checks: `pytest tests/rendering/test_images.py tests/test_prompt_budgets.py tests/test_guards.py -q`
-> 32 passed; the full suite is green. On the sample `--until images`: **3 photo slots**, **8 logos
copied from the source bytes**, **0 generated** (no image gateway; the source photos have no caption
block and no description, so nothing would be fabricated).

Known gaps: live FLUX/vision calls need the Cloudflare keys (not on this machine); the sample's photos
have neither captions nor descriptions, so the stage lists them as skipped slots rather than generating.

Next: M8.11 rendering (mirror and flow pages, PDF metadata).

## v3.2 addendum - M8.11 rendering (task 14)

Done:

| Item | File | Notes |
| --- | --- | --- |
| Mirror pages (M8.11.1) | `src/rendering/_pages.py`, `templates/page_mirror.html.j2` | One `<section class="sheet">` per template at its page size; every block is `position:absolute` with `left/top/width/height` in points and `overflow:hidden`. Text uses its Text Style (size, colour, line height, alignment) and `@font-face` from the extracted font file (fallback family when absent). Tables are rebuilt from the display frames with the source border width and header fill; charts are inline SVG; photos/logos are images. The decoration layer redraws `get_drawings()` paths outside table/chart boxes as one SVG behind the blocks. |
| Flow pages (M8.11.2) | same, `templates/page_flow.html.j2` | Blocks in reading order inside the margins, page furniture (header/footer/logo/page number) left out; each table is immediately followed by its chart; `break-inside: avoid` on blocks. The content sits in a fixed inner box so one template stays one PDF page. |
| PDF metadata (M8.11.3) | `_pages.write_metadata_v32` | Info keys title/author/subject/keywords plus XMP with `decraft:synthetic="true"` and `decraft:run_id`. Applied after WeasyPrint renders. |
| Page images | `_pages.render_pages` | `output.pdf`, `pages/p<N>.png` at 110 dpi, and `side_by_side_p<N>.png` (source page \| output page) for every page. |
| Stage | `scripts/stage.py` | `--until render` loads style, templates, fitted text, display tables, chart SVG sidecars, photo/logo files and the plan metadata and renders the run. |
| Tests | `tests/rendering/test_pages.py` | 4 tests: mirror HTML has absolute pt positions and an `@font-face` file that exists; flow order is text -> table -> chart with `break-inside: avoid`; metadata contains the synthetic marker and run id; a full render writes the PDF, page PNG and metadata. |

Checks: `pytest tests/rendering/test_pages.py -q` -> 4 passed; the full suite is green.
On the sample `--until render`: 5 templates -> **5 PDF pages**, **5 page PNGs**, **5 side-by-side
images**, 4 decorated mirror pages (the flow page has no drawing layer); metadata title
"Tata Power Company Limited", author "Operations Division", `decraft:synthetic` and `decraft:run_id`
present in XMP. A visual check of `side_by_side_p1.png` and `p2.png` shows the mirror blocks at their
source positions and the flow page reflowed in reading order.

Known gaps: a flow page whose content is taller than one page is clipped (QA/critic in M8.12 will
report and adjust line height/spacing); some source text blocks overlap tables because their
template boxes do, which M8.12's layout gate will measure.

Next: M8.12 QA and the Visual Critic.

## v3.2 addendum - M8.12, M9, M10 review and v3.3 (2026-10-26)

M9/M10 were built by a second session; this entry records the review and the v3.3 work on top.

| Area | Files | What it does now |
| --- | --- | --- |
| M9.1 stage machine (review) | `src/pipeline.py` | 17 stages, `done.json` with input/output hashes, config-slice reruns, partial runs on `LLMBudgetExceeded`/`CacheMiss`. Fixed: a stage timeout hung the run (`with ThreadPoolExecutor` waits for the stuck worker); test runs overwrote the repo's `report_output.pdf`. |
| Gates | `src/gates.py`, `gates.json` | v3.2 section 9 + v3.3 section 5 (29 gates) from the stage outputs; model-only gates (`words_per_page`, `no_cross_section_repeats`) are "skipped (llm disabled)" when the model is off (M9.2). `metadata_synthetic` is `metadata_clean` (E-32). |
| M9.3 | `tests/pipeline/test_injection.py` | Echo model; the injected instruction reaches no text/title/bible, prompts unchanged, no key in any artifact. Passes. |
| M10.1 (review) | `src/prompt_registry.py`, `scripts/build_prompt_log.py`, `scripts/archive_prompts.py` | Registry moved out of `src/llm` (module-boundary guard). Vision prompts were listed as Groq; now from `PromptSpec.role`. `requirements.txt` is a `pip freeze` (was a 16-line hand list). |
| M10.2 | `scripts/make_notebook.py`, `solution.ipynb` | 13 cells; executes end to end with no keys (model-off). Not yet executed with live models. |
| M10.3 (review) | `scripts/package.py` | 370 entries, 3.4 MB, relative paths, no `.env`, no key values. |
| Tests and quota | `tests/conftest.py` | Tests were making LIVE provider calls (default config + keys in the environment): at least 27 today. An autouse fixture now removes the keys for every test. |
| Windows | `_measure.py`, `_extract.py`, `report_stages.py` | Fonts held open by fontTools and matplotlib locked `style/fonts/*.ttf`; closed/released. |
| v3.3 F-01..F-03 | `_tiny.py`, `src/core/dcr.py`, `_autounits.py`, `_placeholders.py` | Per-cell tiny-table jitter; DCR in audit and validation (E-46); `{{key|auto}}`, `section_writer` v3. |
| v3.3 F-04 | `src/style/_ocr.py`, pipeline | Scanned pages get an invisible OCR layer (RapidOCR, 100% word recovery on the test scan); `ocr.json`. |
| v3.3 F-05 | `src/style/_layout_check.py`, `scripts/fetch_layout_model.py` | DocLayout-YOLO agreement, flags only (E-48/E-49). Weights NOT downloaded (needs the owner's approval): the check reports "not run". |
| v3.3 F-06/F-07 | `src/style/_chart_geometry.py` | Exact geometry recovery of the two vector price charts (confidence 0.999-1.0); redrawn charts keep the source colours and plain lines: style similarity 1.0 / 0.98. |
| v3.3 F-08/F-09 | `src/rendering/_structure.py`, `src/report_checks.py` | Round trip + metrics (E-50); discriminator (vision, 2 calls/page) when a vision model is on. |
| v3.3 F-10/F-11 | `src/rendering/_css.py`, `src/narrative/_repeats.py` | Hyphenation + `<html lang>`; cross-section repeats with one `rep` retry, drop or flag. |
| Layout (owner) | E-48, `page_texts` | Every page mirrored; every heading box gets a heading; brand images in the header/footer bands are logos. |

Sample, model off, fresh (`scripts/run_decraft.py --no-llm --fresh`): 5 pages, 27 of 29 gates true, 2 skipped (model-only).
False: `roundtrip_structure` and `layout_metrics`. Text placement passes on 4 of 5 pages; the
structural counts and block IoU (0.33-0.66) differ mostly because our rule extractor reads the
RENDERED pages differently (merged table regions, heading classes), not because boxes moved.

Open: live run with the models (writer word limits, titles, discriminator, critic); F-05 weights;
`assets/fonts/` fallback fonts not shipped (the sample uses its own extracted fonts); M11 (second PDF,
acceptance). Model-off text uses fallback labels (e.g. "Item E") because the Label Fabricator needs the model.
