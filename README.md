# DeCraft

DeCraft (short for Decoy-Craft) makes fake documents that look real.

You give it a PDF, say a five-page equity research note. It gives back a new PDF that looks like it came from the same publisher: same page size, fonts, colours, tables, charts and images, each in the same place. Only the content changes. Every number, name and sentence is new. The idea is to plant these files as bait. A convincing "Q1 results" note on a file share is something an intruder will open, and if anyone ever opens it, you know someone is where they shouldn't be.

A decoy only holds up to a careful look if its numbers behave like real ones, so DeCraft does more than swap random numbers into the old file. It works out how the data in the source behaves (which rows add up to which totals, which figures appear in two places, which series trend upwards) and generates new data that behaves the same way. The new values stay far enough from the real ones that nothing confidential leaks.

## How it works

One run goes through seventeen steps, grouped into four parts:

1. **Read the source** (`extract`). Text, tables, charts and images, with their fonts, sizes and positions on the page.
2. **Understand the data** (`infer`, `privacy_plan`). For every table it profiles the columns and finds which relations hold in the printed numbers: sums, totals, growth rates, margins, and the same figure repeated across tables. A language model may *suggest* a rule, but Python keeps it only if it holds on the actual cells. It also decides which columns are sensitive.
3. **Make new data** (`generate`, `privacy_audit`). New tables that keep every verified relation. Then a privacy audit: no number too close to the original, no row that's a near-copy, no real name left. Periods move forward one year (FY24 becomes FY25, 22 May 2023 becomes 22 May 2024).
4. **Rebuild the document** (`style` → `templates` → `figures` → `prepare` → `charts` → `plan` → `write` → `fit` → `images` → `render` → `qa` → `package`). It learns the source's layout box by box and writes new text with placeholders for every number, so the prose and the tables can't disagree. Then it redraws the tables and charts in the source's style, renders the PDF and checks it against the source.

Every run leaves a folder with all the intermediate files, a manifest, and the list of quality checks ("gates") that passed, failed or were skipped.

## Setting it up

You need Windows and Python 3.12. The first run is slow because Docling downloads its table and layout models; after that they're cached.

```powershell
cd D:\Acalvio\DeCraft
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, allow it once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The PDF renderer (WeasyPrint) needs the GTK libraries. A copy is bundled in `vendor\gtk` and picked up automatically.

### Model keys (optional)

DeCraft runs without any AI model. The text is plainer that way, but nothing breaks. To turn the models on, set these in the same terminal before you run it:

```powershell
$env:GROQ_API_KEY  = "..."   # text model (openai/gpt-oss-120b on Groq)
$env:CF_ACCOUNT_ID = "..."   # Cloudflare Workers AI: vision model and image generator
$env:CF_API_TOKEN  = "..."
```

If you don't have a Groq key but have [Ollama](https://ollama.com) running locally with `llama3.1`, DeCraft uses that for text instead.

Both cloud providers are used on their free tiers, which run out quickly: Groq allows 200k tokens a day and Cloudflare 10,000 "neurons" a day. One full run with models uses a good share of both. When a quota runs out, DeCraft switches that model off and carries on with its built-in fallback.

## The studio (web UI)

The easiest way to use DeCraft is the studio, a small web page that runs on your own machine:

```powershell
python scripts\serve_ui.py
```

It opens http://127.0.0.1:8765. Drag in a folder (or pick PDFs) and every PDF in it is turned into a decoy, one after another. Each document shows its progress step by step. When it's done you can compare the original and the decoy page by page, download the decoy or the full run package, or download every decoy of the upload as one zip that keeps your folder structure.

The "Use AI models" switch works when the model keys are set in the terminal you start the studio from. With it off, nothing leaves your computer. Uploads and runs are kept in `artifacts_ui\` (not committed to git), and the job list survives a restart.

## Running from the command line

```powershell
python scripts\run_decraft.py data\input\sample_input_1.pdf          # offline
python scripts\run_decraft.py data\input\sample_input_1.pdf --llm    # with the models
```

It prints the run folder at the end. The decoy is `report_output.pdf` in that folder, and `submission.zip` next to it holds the whole package.

| Flag | What it does |
|---|---|
| `--llm` | Use the AI models (off by default). |
| `--no-llm` | Force the offline path even if keys are set. |
| `--fresh` | Redo every step instead of reusing the ones that already finished. |
| `--until <step>` | Stop after one step, e.g. `--until infer`. |
| `--artifacts <folder>` | Write runs and caches somewhere other than `artifacts\`. |

### About caching

DeCraft keeps two caches:

- **Finished steps.** Each step writes a `done.json`. On the next run, a step whose inputs haven't changed is skipped. `--fresh` turns this off.
- **Model answers.** Every model reply is saved under `artifacts\llm_cache\`, so asking the same question again costs nothing. To get new answers, rename the folder and run with `--fresh`:

  ```powershell
  Rename-Item artifacts\llm_cache llm_cache.bak
  python scripts\run_decraft.py data\input\sample_input_1.pdf --llm --fresh
  ```

  A *bad* answer gets cached too, so after changing a prompt, bump its version number (that changes the cache key).

The run folder is named after the PDF's hash, so two runs of the same PDF write to the same place. If you run with and without `--llm` at the same time, give one of them its own `--artifacts` folder.

### Replaying a model run without keys

With a filled `llm_cache` (the submission zip ships one), a model run can be replayed offline:

```powershell
$env:DECRAFT_CACHE_ONLY = "1"
python scripts\run_decraft.py data\input\sample_input_1.pdf --llm
```

A question that isn't in the cache stops the run with status `partial` instead of calling out.

## Reading the result

A run ends with one of three statuses:

- **ok**: every gate passed (skipped gates are listed separately, with the reason).
- **failed**: the PDF was made, but at least one gate didn't pass. `gates.json` in the run folder says which, and `qa\output.json` has the details.
- **partial**: the run stopped early, usually on a model quota or budget. Run it again and it picks up where it left off.

## Results on the two sample notes

Both samples were run offline and then checked by reading the decoy back with the same extractor that read the source.

| | Sample 1 (Geojit, 5 pages, portrait) | Sample 2 (Anand Rathi, 9 pages, landscape) |
|---|---|---|
| Status | ok | ok |
| Gates | 28 passed, 0 failed, 3 skipped | 28 passed, 0 failed, 3 skipped |
| Pages | 5 of 5, same size | 9 of 9, same size |
| Table shapes read back | 11 of 12 identical | 7 of 7 identical |
| Fonts, sizes and colours | same set, no new styles | same set, no new styles |
| Source blocks at the same place | 66 of 72 | 83 of 84 |
| Layout overlap per page | 0.98–0.997 | 0.99–0.999 |

The missing blocks are removed on purpose (see below). In sample 1, the one table that reads back differently has 23 rows instead of 22, probably an extraction difference. The three skipped gates are "every table has a chart" (no extra chart pages, see below) and two writing checks that only run with the text model on.

The decoys of both samples are in `outputs\samples\` (sample inputs in `data\input\`).

What the output does on purpose:

- **The numbers hold together.** Totals add up, Total Assets equals Total Liabilities (even where the source differs by 1 from rounding), and margins, growth rows and EPS are recomputed from the new amounts. A figure printed in two places, such as the target price in the masthead and the valuation table, is the same number everywhere. None of these rules are hard-coded: each is found in the source and kept only if it holds on every cell. Row numbers (Sr. No. 1–10) stay as printed.
- **Tables and mastheads look like the source's.** Tables are redrawn on the source's own cell grid with its fills, bold rows, gridlines and number formats (`1,141`, `(13.4)`, `850bps`, Indian grouping such as `1,24,538`). Masthead lines (dates, CMP/target box, report series) keep their wording with new figures and shifted dates. List bullets keep the source's glyph and indent.
- **No pages are added.** The decoy has exactly the source's pages. Charts are still built for every table and checked against it, but a table whose page had no chart in the source isn't given one.
- **Privacy removals.** E-mail addresses, the website footer and signature images are left out, and people's names (such as the analyst) are replaced with generated ones.

## Project layout

```
DeCraft/
├── config/default.yaml      every threshold and model setting, with comments
├── data/input/              sample source PDFs
├── docs/
│   ├── project_decisions.md design decisions and why (the E-xx numbers in code comments)
│   ├── report/              short technical write-up (LaTeX) with figures
│   ├── prompts/             archived prompt versions
│   └── …                    spec, milestone reports, glossary, prompt change notes
├── scripts/
│   ├── run_decraft.py       run the full pipeline on one PDF
│   ├── serve_ui.py          start the studio
│   ├── stage.py             run the report steps up to a named step (development)
│   ├── run_blueprint_and_plan.py  blueprint + document plan only (golden test baseline)
│   ├── package.py           build submission.zip
│   ├── make_notebook.py     write solution.ipynb
│   ├── build_prompt_log.py  write PROMPT_LOG.md
│   ├── archive_prompts.py   save prompt versions under docs/prompts
│   ├── fetch_layout_model.py  download the optional DocLayout-YOLO weights
│   └── smoke_llm.py         one tiny live request per model provider
├── src/
│   ├── pipeline.py          the 17-step run (stage machine, caching, manifest)
│   ├── report_stages.py     the report steps (style … package)
│   ├── report_checks.py     output checks that span several modules
│   ├── gates.py             the quality gates, each read from the step output that proves it
│   ├── config.py, errors.py, prompt_registry.py, stage_runner.py
│   ├── contracts/           data types passed between steps
│   ├── core/                pure helpers: number grids, periods, units, originality …
│   ├── blueprint/           source blueprint (text blocks, tables, styles, entity)
│   ├── planner/             document plan
│   ├── extraction/          Docling / PyMuPDF table and figure extraction
│   ├── inference/           column profiles, rule proposal and verification
│   ├── privacy/             privacy decisions and the post-generation audit
│   ├── generation/          new data that keeps every verified relation
│   ├── validation/          checks on the generated data
│   ├── style/               fonts, colours, page templates, figure analysis
│   ├── narrative/           writing (model or offline), placeholders, consistency
│   ├── rendering/           charts, tables, HTML/PDF, layout QA
│   ├── llm/                 gateway, providers, budgets and the answer cache
│   └── webui/               the studio (standard library HTTP server + static page)
├── tests/                   pytest suite, one folder per package
├── artifacts/               default run folder and caches (model answers, extraction)
├── artifacts_llm/           a run made with the models, kept for replay
├── outputs/                 blueprint/plan baseline and the sample decoys
├── solution.ipynb           walks through a full run
├── PROMPT_LOG.md            every prompt, version and example payload
└── report_output.pdf        the submitted decoy of sample 1
```

## Tests

```powershell
$env:MPLBACKEND = "Agg"; $env:OPENBLAS_NUM_THREADS = "1"
python -m pytest -m "not slow"
```

The test suite removes the API keys before it starts, so it never spends quota. On this machine pytest doesn't always print its summary line, so check the exit code (`$LASTEXITCODE`, 0 means everything passed).

## Known limits

- **Offline text is generic.** Without the text model the prose comes from a small set of fixed sentences: it repeats, and pages have fewer words than the source. This shows most on long disclaimer pages. Run with `--llm` for proper text. The tables, charts and mastheads are complete either way.
- **Unreadable charts get a stand-in.** If a source chart's data can't be read (a raster image or an unlabelled plot), a stand-in line is drawn on the page's own axis so the slot isn't empty. It's listed as invented in the run notes.
- **Optional layout model.** The DocLayout-YOLO check needs its weights (`python scripts\fetch_layout_model.py`); without them that check is skipped, not passed.
- **Vision model.** The Cloudflare vision model has to be Llama 4 Scout. The older Llama 3.2 11B Vision ignores JSON mode and answers in prose.
