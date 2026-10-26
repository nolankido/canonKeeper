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

[![CI](https://github.com/nolankido/canonkeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/nolankido/canonkeeper/actions/workflows/ci.yml)
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
1. **Extract.** Each chapter goes to the extraction model (`gemini-3.8-flash` by default) with a strict schema: atomic claims
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

Check your next manuscript against the bible without touching it:

```bash
canonkeeper check evals/corpora/sherlock-holmes/series.json drafts/book-07.md
```

`--provider mock` works on any command and is what the test suite and `demo` use.
`--provider openweights` points the same pipeline at Ollama, LM Studio, or any OpenAI-compatible
host — the right choice for manuscripts that must not leave your machine.

> The Gemini free tier may use prompts to improve Google's products. canonkeeper says so every
> time it sends text through it. Public-domain corpora: fine. Unpublished manuscripts: use a
> paid tier or a local model.

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

| corpus | injected | reported | TP | FP | FN | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Sherlock Holmes (4 novels) | — | — | — | — | — | — | — | — |

*Results land here once the extraction prompts are tuned; the harness is in place and runs green
against the mock provider in CI.*

## Project layout

```
src/canonkeeper/
  corpus/      manifest, Gutenberg fetcher, chapterizer (offset-preserving)
  extract/     per-chunk claim extraction + quote grounding        (Muse Spark)
  canon/       JSONL bible with alias resolution and slot index
  audit/       candidate heuristics + batched adjudicator with 1M-ctx assembly (Muse Wonder)
  report/      audit report + bible viewer (HTML), JSON
  demo.py      the invented Saltmarsh series with planted errors
  eval/        defect injection + precision/recall scoring
  llm/         OpenAI-compatible client (Gemini / local / Meta), pacing + backoff, rule-based mock, disk cache, cost tally
  cli.py       doctor · demo · fetch · estimate · build · bible · audit · check · eval
evals/corpora/ public-domain series manifests used for benchmarking
docs/          ARCHITECTURE.md · DEMO_SCRIPT.md
```

## Models and providers

| role | default | why |
|---|---|---|
| extraction | `gemini-3.8-flash` | free tier, 1M context, structured JSON out |
| adjudication | `gemini-3.8-flash` (`gemini-4-argon` with billing) | reads the whole series before ruling |
| local mode | anything on Ollama / LM Studio via `CANONKEEPER_PROVIDER=openweights` | private manuscripts never leave the machine |
| Meta Model API | `muse-spark-1.3` / `muse-wonder-1.0` via `CANONKEEPER_PROVIDER=meta` | kept as an option; needs billing |

Free tiers are rate-limited, so the client paces itself (`CANONKEEPER_REQUESTS_PER_MINUTE`,
default 9 on Gemini), retries 429/5xx with exponential backoff honouring `Retry-After`, and
falls back gracefully if a server rejects JSON mode. A six-book series is ~250 extraction
calls: about half an hour on the free tier, once, because every response is cached on disk.

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

A series audit has a knowable price before it runs — `estimate` counts the tokens — which makes
it a natural pay-per-job service: upload a series, get a quote, pay, get the report. That layer
is being built under `preflight/` for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/)
(submissions due **Nov 12, 2026**): a small web app that quotes from the token count, takes
payment through PayPal's sandbox, runs the audit, and delivers the report.

## Status

Working offline pipeline; real-model runs on the Gemini free tier. See
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) for the demo plan and the roadmap below for what lands when.

- [x] corpus loading, chapterizing, provenance offsets
- [x] claim extraction with quote grounding
- [x] bible store, alias resolution, slot index
- [x] candidate heuristics (slot mismatch, dead-speaks, timeline, name drift)
- [x] predicate / value normalisation at ingest
- [x] batched adjudicator, series-as-prefix for prompt caching, budgeted context assembly
- [x] audit report + bible viewer (HTML/JSON)
- [x] seeded-defect eval harness
- [x] offline demo with a rule-based provider
- [x] CLI, tests, CI
- [x] chapterizer verified against the real Gutenberg layouts of all six Holmes volumes
- [x] `check` on an unpublished draft, with the deliberate-reveal case in the demo
- [x] Gemini free-tier provider with pacing, backoff, JSON-mode fallback; `doctor` command
- [ ] first real run on Sherlock Holmes; prompt tuning; publish benchmark numbers
- [ ] Preflight: quote → PayPal sandbox checkout → audit → report
- [ ] local-model mode verified against Ollama
- [ ] demo video

## License

MIT. Public-domain corpora are fetched from [Project Gutenberg](https://www.gutenberg.org/) at
run time and are not redistributed here.
