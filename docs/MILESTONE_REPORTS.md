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




