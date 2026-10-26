# DeCraft

DeCraft (short for Decoy-Craft) makes fake documents that look real.

You give it a PDF, say a five-page equity research note. It gives back a new PDF that looks like it came from the same publisher. The layout, fonts, colours, tables and charts all match the original, but every number, name and sentence is new. The idea is to plant these files as bait: a convincing "Q1 results" note sitting on a file share is something an intruder will open, and if anyone ever opens it, you know someone is where they shouldn't be.

A decoy only works if it holds up to a careful look, so DeCraft does more than swap random numbers into the old file. It tries to work out how the data in the source actually behaves (which columns add up to which totals, which series trend upwards, which figures move together) and generates new data that behaves the same way, while staying far enough from the real values that nothing confidential leaks.

## How it works, in short

The program runs in seventeen steps, but they fall into four parts.

1. **Read the source.** It pulls out text, tables, charts and images, with their fonts, sizes and positions on the page.
2. **Understand the data.** For every table it profiles the columns, checks which formulas really hold (Gross profit = Revenue − Costs, say), and finds trends, breaks and correlations. A language model may *suggest* a rule, but a rule is only kept if Python checks it against the actual numbers.
3. **Make new data.** It generates fresh tables that obey the verified rules and copy the patterns, then audits the result for privacy: no number too close to the original, no row that's a near-copy, no real name left behind. Period labels move forward one year (FY24 becomes FY25).
4. **Rebuild the document.** It writes new text with placeholders for every number, so the prose and the tables can never disagree, redraws the charts in the source's style, places everything where the source had it, renders the PDF, and then checks the result.

Every run leaves a folder with all the intermediate files, a manifest of what happened, and a list of quality checks ("gates") that passed or failed.

## Setting it up

You need Windows and Python 3.12. The first run is slow because Docling downloads its table and layout models; after that they're cached.

```powershell
cd D:\Acalvio\DeCraft
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, allow it once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The PDF renderer (WeasyPrint) needs the GTK libraries. A copy is already bundled in `vendor\gtk` and picked up automatically, so you shouldn't need to install anything.

### Model keys (optional)

DeCraft runs without any AI model at all. The results are plainer that way, but nothing breaks. To turn the models on, set these in the same terminal before you run it:

```powershell
$env:GROQ_API_KEY  = "..."   # text model (openai/gpt-oss-120b on Groq)
$env:CF_ACCOUNT_ID = "..."   # Cloudflare Workers AI: vision model and image generator
$env:CF_API_TOKEN  = "..."
```

If you don't have a Groq key but have [Ollama](https://ollama.com) running locally with `llama3.1`, DeCraft will use that for text instead.

Both cloud providers are used on their free tiers, and those run out quickly: Groq allows 200k tokens a day and Cloudflare 10,000 "neurons" a day. One full run with models uses a good share of both. When a quota runs out, DeCraft notices, switches that model off and carries on with its built-in fallback.

## Running it

The simplest run, with no models:

```powershell
python scripts\run_decraft.py data\input\sample_input_1.pdf
```

With the models switched on:

```powershell
python scripts\run_decraft.py data\input\sample_input_1.pdf --llm
```

It prints the run folder at the end. The finished decoy is `report_output.pdf` in that folder, and `submission.zip` next to it holds the whole package.

A few flags worth knowing:

| Flag | What it does |
|---|---|
| `--llm` | Use the AI models (off by default). |
| `--no-llm` | Force the offline path even if keys are set. |
| `--fresh` | Redo every step instead of reusing ones that already finished. |
| `--until <step>` | Stop after one step, e.g. `--until infer`. |
| `--artifacts <folder>` | Write runs and caches somewhere other than `artifacts\`. |

### About caching

DeCraft keeps two caches, and they're easy to mix up.

- **Finished steps.** Each step writes a `done.json`. On the next run, any step whose inputs haven't changed is skipped. `--fresh` turns this off.
- **Model answers.** Every reply from a model is saved under `artifacts\llm_cache\`. Asking the same question again costs nothing. There's no flag for this one. If you want genuinely new answers, rename the folder:

  ```powershell
  Rename-Item artifacts\llm_cache llm_cache.bak
  python scripts\run_decraft.py data\input\sample_input_1.pdf --llm --fresh
  ```

  Note that a *bad* answer gets cached too, so after changing a prompt, bump its version number. That changes the cache key.

One gotcha: the run folder is named after the PDF's hash, so two runs of the same PDF write to the same place. If you run with and without `--llm` at the same time, they'll overwrite each other. Give one of them its own `--artifacts` folder.

### Re-running without any keys

If you have a filled `llm_cache` (the submission zip ships one), you can replay a model run exactly, offline:

```powershell
$env:DECRAFT_CACHE_ONLY = "1"
python scripts\run_decraft.py data\input\sample_input_1.pdf --llm
```

Any question that isn't in the cache stops the run with status `partial` instead of calling out.

## Reading the result

The run ends with one of three statuses:

- **ok**: every quality gate passed.
- **failed**: the PDF was made, but at least one gate didn't pass. Open `gates.json` in the run folder to see which, and `qa\output.json` for the details.
- **partial**: the run stopped early, usually a model quota or budget. Run it again and it picks up where it left off.

On the sample note, an offline run takes about 25 seconds and passes 29 of 31 gates. The two that fail compare the layout of the finished PDF, read back in, against the source, block by block. They're strict on purpose, and they're where most of the remaining work is.

## Tests

```powershell
$env:MPLBACKEND = "Agg"; $env:OPENBLAS_NUM_THREADS = "1"
python -m pytest
```

The test suite strips the API keys before it starts, so running it never spends quota. On this machine pytest doesn't always print its summary line, so check the exit code (`$LASTEXITCODE`, 0 means everything passed).

## Where things live

- `src/` is the code, one package per part of the pipeline (`extraction`, `inference`, `privacy`, `generation`, `narrative`, `style`, `rendering`, …).
- `config/default.yaml` holds every threshold and model setting, with comments.
- `docs/project_decisions.md` is the log of design decisions and why each was made (the E-xx numbers you'll see in code comments).
- `docs/report/` has a short technical write-up of the method and results.
- `solution.ipynb` walks through a full run step by step.

## Known limits

- The layout read-back checks fail on the sample. Blocks land roughly in the right places, but not within the 0.9 overlap the gate asks for.
- The optional layout model (DocLayout-YOLO) needs its weights downloaded with `python scripts\fetch_layout_model.py`. Without them that check is skipped, not passed.
- Without the text model, the generated prose is shorter than the source, so pages come out with fewer words.
- The Cloudflare vision model has to be Llama 4 Scout. The older Llama 3.2 11B Vision ignores JSON mode and answers in prose.
