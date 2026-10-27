# canonkeeper

**Give it the whole series. It finds the contradictions.**

canonkeeper is a continuity engine for long-running fiction. It reads an entire book series,
builds a provenance-linked *series bible* (every fact cited to the exact passage that asserted
it), and then audits the series — or a new manuscript — for things that cannot all be true:
eye colours that drift, characters who die and keep talking, a birthplace that moves between
book two and book five.

It exists because series authors ship books years apart, readers track every detail, and the
only tool for this today is a spreadsheet and a good memory.

Built by a working indie publisher with 80+ titles across four imprints. Entered in the
[PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/) as **Preflight**: pay-per-audit
continuity checking for series authors (see *Preflight* below).

[![CI](https://github.com/nolankido/canonKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/nolankido/canonKeeper/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

---

## How it works

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
   actual Project Gutenberg layouts of all six Holmes volumes.
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
   the bible itself (one card per entity, every fact sourced, disputed slots flagged).

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

## What it found in Sherlock Holmes

Six Conan Doyle volumes from Project Gutenberg (457,890 words, 630k tokens), on the Gemini
free tier, with no hints:

| | |
|---|---|
| facts extracted | 2,331 across 420 entities (99.2% of returned claims grounded to a verbatim passage) |
| candidate conflicts screened | 26 |
| **contradiction** | **1, high: Watson's war wound** — "struck on the shoulder by a Jezail bullet" (*A Study in Scarlet*) vs "sat nursing my wounded leg" (*The Sign of the Four*) vs "the jezail bullet which I had brought back in one of my limbs" (*The Noble Bachelor*). Suggested fix, to the later book: change "wounded leg" to "wounded arm". |
| deliberate | 2 — Holmes's "dark" eyes in *The Sign of the Four* (he is disguised as an old sailor); Lucy Ferrier's hair, golden at five and chestnut as a young woman |
| not a conflict | 20 — flashbacks after a death, synonyms ("flaxen" / "fair"), and the bible's own mislabels, each explained |
| list-price cost | $0.74 to read, $0.21 to judge |

That is the continuity error readers have argued about since 1890, found unassisted, with both
passages on the page:

![The Holmes audit report: Watson's wound, four passages, the adjudicator's verdict and a fix to the later book](docs/img/report.png)

Every fact behind it is browsable in the bible page, with its source passage one click away:

![The series bible: Sherlock Holmes, 242 facts, aliases, every slot with its sources](docs/img/bible.png)

The famous landlady slip (Mrs. Hudson in *The Sign of the Four*, Mrs. Turner in *A Scandal in
Bohemia*) is now raised by the `role_collision` screen and goes to the adjudicator on the next
run. Mary Watson calling her husband "James" (*The Man with the Twisted Lip*) is not caught:
the extractor does not yet record a vocative inside dialogue as a `first_name` claim. That is
the kind of gap the benchmark below is for.

## Does it actually work? The seeded-defect benchmark

Eyeballing output is not evidence. `canonkeeper eval` takes a clean series and its bible,
**plants N contradictions in the prose of later books** (a new eye colour, a different
birthplace, a character quietly buried and then walking around), re-runs the *entire* pipeline
on the mutated text, and scores what came back against ground truth. Contradictions that were
already in the source — Conan Doyle has several — are measured in a baseline run and excluded
from the false-positive count.

```bash
canonkeeper eval evals/corpora/sherlock-holmes/series.json --n 20 --seed 0
```

| corpus | run | injected | reported | TP | FP | FN | precision | recall | F1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Sherlock Holmes (6 volumes) | general pass only, low effort | 20 | 1 | 1 | 0 | 19 | 1.00 | 0.05 | 0.10 |
| Sherlock Holmes (6 volumes) | + descriptive sweep, medium effort | *(running)* | | | | | | | |

The first number is bad, and it is the reason the harness exists: every one of the 19 misses
was lost at **extraction**, not at judgement — a single planted sentence in a chapter about
someone else was skipped by the general pass. A second, narrow extraction pass over
descriptive facts and a higher reasoning budget took extraction recall on the planted chunks
from 7/20 to 15/20 (`scripts/probe_extraction.py` measures that in two minutes without a
full audit). The second row is the full pipeline with those changes.

## Project layout

```
src/canonkeeper/
  corpus/      manifest, Gutenberg fetcher, chapterizer (offset-preserving)
  extract/     per-chunk claim extraction + quote grounding        (extraction model)
  canon/       JSONL bible with alias resolution and slot index
  audit/       candidate heuristics + batched adjudicator with 1M-ctx assembly (adjudication model)
  report/      audit report + bible viewer (HTML), JSON
  demo.py      the invented Saltmarsh series with planted errors
  preflight/   the paid service: pricing, PayPal Orders v2 client (+fake), job store/runner, FastAPI app
  eval/        defect injection + precision/recall scoring
  llm/         OpenAI-compatible client (Gemini / local / Meta), pacing + backoff, rule-based mock, disk cache, cost tally
  cli.py       doctor · demo · fetch · estimate · build · bible · audit · check · eval · serve
evals/corpora/ public-domain series manifests used for benchmarking
docs/          ARCHITECTURE.md · DEMO_SCRIPT.md
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
free tier the answer is $0; at list price for a ~900k-token, six-novel series:

| step | reads | calls | ≈ list price |
|---|---|---|---|
| extraction (`gemini-3.8-flash`, $0.75/M in) | every token once | ~250 chapters | $0.90 |
| audit, 40 candidates (`gemini-3.8-flash`) | series once per batch of 8 | 5 | $3.50 |
| same audit on `gemini-4-argon` ($2/M in) | series once per batch of 8 | 5 | $9 |

Iterate on prompts at the small context cap, run the full-context audit once for the demo,
and let the disk cache do the rest.

## Preflight (the PayPal AI Hackathon entry)

A series audit has a knowable price before it runs — the token count is known the moment the
files land — which makes it a natural pay-per-job service. `canonkeeper serve` runs it:

```
upload series ──▶ quote ($5 + $4 per 100k tokens, shown with the arithmetic)
      ──▶ PayPal Orders v2 checkout (sandbox) ──▶ capture ──▶ audit runs in the background
      ──▶ report + bible pages, job page refreshes itself
```

```bash
canonkeeper serve                      # demo mode: payments simulated in-process, mock provider works too
PAYPAL_CLIENT_ID=… PAYPAL_CLIENT_SECRET=… canonkeeper serve   # real PayPal sandbox (or put them in .env)
canonkeeper doctor --paypal            # proves the credentials: OAuth + a sandbox order, never captured
```

`render.yaml` deploys the same service as a free Render web service (set the three keys in the
dashboard, or none for demo mode); `--public-url` is what PayPal sends the buyer back to.

<p align="center">
<img src="docs/img/preflight-quote.png" width="49%" alt="Quote page: price with the arithmetic shown, Pay with PayPal">
<img src="docs/img/preflight-job.png" width="49%" alt="Job page after payment: facts in bible, contradictions, open the report">
</p>

Nothing is sent to a model until the order is captured. Jobs persist as JSON on disk, run one
at a time (free-tier pacing is per project, so parallelism would only contend), and a failed
job can be retried at no extra cost because every model response is cached. The PayPal client
is ~100 lines of Orders v2 (OAuth → create with `PAY_NOW` → capture, idempotent on
`PayPal-Request-Id`); `FakePayPal` mirrors the state machine for tests and demos.

**Agentic commerce.** `canonkeeper mcp` exposes the same service to an AI assistant over MCP:
`quote_series → order_audit → (the person approves on PayPal) → confirm_payment → audit_status
→ get_findings`, plus `delete_job` (manuscripts, bible, reports and cached responses go;
a receipt stays — the web job page has the same button). The agent prices the job and creates the order; it never holds the buyer's
credentials and cannot approve the payment itself — the approval link goes to the person.
Add it to Claude Desktop or Cursor with `{"command": "canonkeeper", "args": ["mcp"]}`, or watch
the whole flow from a client: `python scripts/mcp_flow_demo.py books/*.txt` (with
`canonkeeper serve` running) spawns the server over stdio, quotes, orders, shows that
`confirm_payment` refuses to run anything before the person approves, then collects the findings.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/) (submissions due
**Nov 12, 2026**).

## Status

Working offline pipeline; real-model runs on the Gemini free tier. See
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) for the demo plan and the roadmap below for what lands when.

- [x] corpus loading, chapterizing, provenance offsets
- [x] claim extraction with quote grounding
- [x] bible store, alias resolution, slot index
- [x] candidate heuristics (slot mismatch, dead-speaks, timeline, name drift, origin
      mismatch, household role collision)
- [x] predicate / value normalisation at ingest
- [x] batched adjudicator, series-as-prefix for prompt caching, budgeted context assembly
- [x] audit report + bible viewer (HTML/JSON)
- [x] seeded-defect eval harness
- [x] offline demo with a rule-based provider
- [x] CLI, tests, CI
- [x] chapterizer verified against the real Gutenberg layouts of all six Holmes volumes
- [x] `check` on an unpublished draft, with the deliberate-reveal case in the demo
- [x] Gemini free-tier provider with pacing, backoff, JSON-mode fallback; `doctor` command
- [x] first real run on Sherlock Holmes (Watson's wound found; 2,331 sourced facts)
- [ ] second benchmark row with the descriptive sweep (running on the free tier)
- [x] Preflight: upload → quote → PayPal checkout → capture → audit → report (`canonkeeper serve`)
- [x] PayPal sandbox credentials and Orders v2 create verified (`canonkeeper doctor --paypal`)
- [ ] full sandbox checkout recorded with a sandbox buyer account (for the video)
- [x] Preflight as an MCP server (`canonkeeper mcp`): an author's assistant quotes, orders,
      hands the person the PayPal approval link, confirms, and reads the findings as data
- [x] MCP flow exercised end to end from a real MCP client (`scripts/mcp_flow_demo.py`)
- [x] customers can delete their files; uploads capped for a public host; `render.yaml`
- [x] local-model mode run against Ollama (`qwen3:4b`, CPU) — see *Local mode, measured*
- [ ] demo video

## License

MIT. Public-domain corpora are fetched from [Project Gutenberg](https://www.gutenberg.org/) at
run time and are not redistributed here.
