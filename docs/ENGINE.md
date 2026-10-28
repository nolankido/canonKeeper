# The engine: how canonkeeper works


```
 series.json ──▶ fetch/chapterize ──▶ extract claims ──▶ bible.jsonl
                                    (cheap long-ctx model)    │
                                                              ▼
                     report.html ◀── adjudicate ◀── candidate conflicts
                                  (stronger model)   (local heuristics, $0)
```

0. **Chapterize.** Headings are taken from the book's own Contents list when it has one, so a
   `CHAPTER I.` with its title on the next line, a `I. A SCANDAL IN BOHEMIA`, or an unnumbered
   `THE ADVENTURE OF THE EMPTY HOUSE` all resolve to the right chapter, and the Contents block,
   `PART` dividers and shouted body lines (`RACHE.`) do not split anything. Tuned against the
   actual Project Gutenberg layouts of all six Holmes volumes. Authors' own files work too:
   Markdown `#` headings, `Chapter 1`, a bare `1` or `II.`; Word's UTF-16 text and
   Windows-1252 files are read as they are, and a chapter pasted without blank lines between
   paragraphs is still cut into readable chunks.
1. **Extract.** Each chapter goes to the extraction model (`gemini-3.5-flash-lite` by default) with a strict schema: atomic claims
   (`Watson · wound_location = shoulder`), a fixed predicate vocabulary, and a **verbatim quote**
   for every claim. Claims whose quote cannot be found in the source chunk are dropped — that is
   the hallucination guard. Nothing enters the bible without a passage behind it.
2. **Screen.** Cheap local heuristics over-generate candidate conflicts: same entity + same
   single-valued predicate + different value; a character who dies and then acts; conflicting
   years; name drift. No model calls.
3. **Adjudicate.** Candidates go to the adjudication model **in batches**, with the series text sent
   once per batch as a stable prefix (so a cache-aware host serves it cheaply) and the batch of
   candidates last: each with its quotes and everything else the bible knows about that entity.
   With a 1M-token model a typical series fits whole; when it doesn't,
   the chapters the batch's quotes came from go in first. The verdict per candidate is one of
   `contradiction` / `deliberate` (a reveal, a lie, a disguise, change over time) / `ambiguous` /
   `not_conflict` (the bible was wrong), with severity, an explanation, and a minimal fix to the
   *later* book. Earlier books are published; you never edit those.
4. **Report.** Two HTML pages: the audit (every finding opens to the lines that caused it) and
   the bible itself (one card per entity, every fact sourced, disputed slots flagged), plus the
   bible as a spreadsheet (`bible.csv`, one row per fact with its passage) and the findings as
   JSON. Both pages print or save to PDF cleanly.

The split is deliberate: the extractor reads cheaply and widely; the adjudicator thinks
expensively and only where it matters. Batching means the cost of reading the series is paid once per eight
candidates, not once per candidate.


## Try it in 30 seconds, no key

```bash
pip install -e ".[dev]"
canonkeeper demo --open-report
```

That writes an invented three-book series with five planted continuity errors, runs the whole
pipeline against a deterministic rule-based stand-in, and opens the report. All five are found.
It then checks an unpublished fourth-book draft against the bible: two slips come back as
contradictions with fixes, and one change the draft *explains* (a character's eye colour was a
trick of tinted glasses) comes back as `deliberate`. That distinction is the whole point.
It is the same code path the real models use; only the provider differs.

## Quickstart with real models (Gemini free tier, no card)

```bash
cp .env.example .env            # paste GEMINI_API_KEY from https://aistudio.google.com/apikey
canonkeeper doctor              # key present? endpoint answers? pacing set?

canonkeeper fetch    evals/corpora/sherlock-holmes/series.json   # pulls the books from Project Gutenberg
canonkeeper estimate evals/corpora/sherlock-holmes/series.json   # tokens + dollars before you spend any
canonkeeper build    evals/corpora/sherlock-holmes/series.json   # extract the bible
canonkeeper bible    evals/corpora/sherlock-holmes/series.json   # browse it
canonkeeper audit    evals/corpora/sherlock-holmes/series.json   # find + adjudicate contradictions
```

Check your next manuscript against the bible without touching it. The repo ships an original
"book seven" Holmes draft with three slips and one explained change planted in it:

```bash
canonkeeper check evals/corpora/sherlock-holmes/series.json evals/demo/holmes-book-07-draft.md
```

On the free tier (2026-10-27) it caught **Holmes's height** ("barely five feet and a half"
against "rather over six feet") and missed the other two slips: Mary Morstan's brown eyes were
never extracted from a relative clause ("the brown eyes that had first drawn me to her"), and a
living Jefferson Hope ("He is still in Pentonville") was extracted as an *attribute*, which the
dead-character screen ignored — fixed since. The white hair raised no false alarm: the extractor
recorded the hair under the wig. Only conflicts that involve the draft are judged.

`--provider mock` works on any command and is what the test suite and `demo` use.
`--provider openweights` points the same pipeline at Ollama, LM Studio, or any OpenAI-compatible
host — the right choice for manuscripts that must not leave your machine.

### Local mode, measured

```bash
ollama pull qwen3:4b                    # 2.5 GB
OLLAMA_CONTEXT_LENGTH=16384 ollama serve  # the default 4k window silently truncates prompts
canonkeeper build  series.json --provider openweights
canonkeeper audit  series.json --provider openweights
```

On a laptop CPU (no GPU, Ollama 0.21) with `qwen3:4b`, on the three-book Saltmarsh demo with five
planted errors:

| stage | result |
|---|---|
| speed | ~90 s per extraction call, ~2.5 min per judgement; thinking is switched off by default (with it on, a one-line JSON reply took over 55 s) |
| extraction | 84 facts, **every one grounded** to a verbatim quote; 3 of the 5 planted errors reached the screen (the "Toby"/"Tom" name change and the moved wound were not extracted) |
| judgement | the 4B model ruled all three real conflicts "deliberate" or "not a conflict" on invented reasons ("likely character development"). Because clearing a conflict must now quote the passage that explains it, all three were moved to **Needs your judgement** instead of being dismissed |

So a small local model gives you a private, sourced bible and a list of candidate
contradictions to read yourself; it does not give you trustworthy verdicts. A larger local
model, or Gemini for public-domain text, does the judging.

> The Gemini free tier may use prompts to improve Google's products. canonkeeper says so every
> time it sends text through it. Public-domain corpora: fine. Unpublished manuscripts: use a
> paid tier or a local model.

## Project layout

```
src/canonkeeper/
  corpus/      manifest, Gutenberg fetcher, chapterizer (offset-preserving)
  extract/     per-chunk claim extraction + quote grounding        (extraction model)
  canon/       JSONL bible with alias resolution and slot index
  audit/       candidate heuristics + batched adjudicator with 1M-ctx assembly (adjudication model)
  report/      audit report + bible viewer (HTML), JSON
  demo.py      the invented Saltmarsh series with planted errors
  preflight/   the paid service: pricing, PayPal client (+fake), payments, budgets, jobs, FastAPI app, MCP server
  eval/        defect injection + precision/recall scoring
  llm/         OpenAI-compatible client (Gemini / local / Meta), pacing + backoff, rule-based mock, disk cache, cost tally
  cli.py       doctor · demo · fetch · estimate · build · bible · audit · check · eval · serve
evals/corpora/ public-domain series manifests used for benchmarking
docs/          ARCHITECTURE.md · PREFLIGHT.md · DEMO_SCRIPT.md · gallery/ (Devpost images)
```

## Models and providers

| role | default | why |
|---|---|---|
| extraction | `gemini-3.5-flash-lite` | free, ~2 s per chapter chunk, structured JSON out |
| adjudication | `gemini-3.6-flash` (`gemini-4-argon` with billing) | 1M context: reads the whole series before ruling |
| local mode | anything on Ollama / LM Studio via `CANONKEEPER_PROVIDER=openweights` | private manuscripts never leave the machine |
| Meta Model API | `muse-spark-1.3` / `muse-wonder-1.0` via `CANONKEEPER_PROVIDER=meta` | kept as an option; needs billing |

**The free tier's real limit is requests per day, not per minute.** Observed on Gemini
(2026-10-26): Flash-Lite 500 requests/day, 3.6 Flash and 3.8 Flash 20/day each, per project,
resetting at midnight Pacific. A six-volume series is ~222 chunks × 2 extraction passes ≈ 444
Flash-Lite requests, and an audit is 4–6 Flash requests — so on the free tier that is **one
full series per day**, and the benchmark the next morning. The client paces requests and
tokens per minute, retries 429/5xx with backoff, moves down a model ladder when a model is
overloaded or out of daily quota, and when every model on the ladder is spent it stops and
prints the reset time; because every response is cached, running the same command again
resumes exactly where it stopped. `canonkeeper doctor` shows today's request count per model;
`scripts/probe_models.py` shows which models are serving right now.

Everything is OpenAI-SDK compatible; set `CANONKEEPER_*` env vars or edit `.env`. Responses are
cached on disk by (model, prompt), so re-running an audit is free.

### What it costs

`canonkeeper estimate` prints this for your series before you spend anything. On the Gemini
free tier the answer is $0; at list price, measured on the six Holmes novels:

| step | model (default) | measured on Holmes (630k tokens) | ≈ list price |
|---|---|---|---|
| extraction | `gemini-3.5-flash-lite` ($0.30/M in, $2.50/M out) | 444 calls, every token read once | $1.40 |
| audit, 27 candidates | `gemini-3.6-flash` ($0.75/M in) | each judged with its chapters in context | $0.15 |
| same audit on `gemini-4-argon` ($2/M in) | | | about $0.40 |

Preflight's price for that series is $5 + $4 per 100k tokens ≈ $30; the model cost is about a
twentieth of it, and nothing on the free tier.

Iterate on prompts at the small context cap, run the full-context audit once for the demo,
and let the disk cache do the rest.
