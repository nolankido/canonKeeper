# canonkeeper

**Give it the whole series. It finds the contradictions.**

canonkeeper is a continuity engine for long-running fiction. It reads an entire book series,
builds a provenance-linked *series bible* (every fact cited to the exact passage that asserted
it), and then audits the series — or a new manuscript — for things that cannot all be true:
eye colours that drift, characters who die and keep talking, a birthplace that moves between
book two and book five.

It exists because series authors ship books years apart, readers track every detail, and the
only tool for this today is a spreadsheet and a good memory.

Built for the [Meta Global AI Developer Hackathon 2026](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
(Indie track · *Build for Your World*) by a working indie publisher with 80+ titles across four
imprints.

[![CI](https://github.com/nolankido/canonkeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/nolankido/canonkeeper/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

---

## How it works

```
 series.json ──▶ fetch/chapterize ──▶ extract claims ──▶ bible.jsonl
                                      (Muse Spark)          │
                                                            ▼
                     report.html ◀── adjudicate ◀── candidate conflicts
                                    (Muse Wonder)    (local heuristics, $0)
```

1. **Extract.** Each chapter goes to `muse-spark-1.3` with a strict schema: atomic claims
   (`Watson · wound_location = shoulder`), a fixed predicate vocabulary, and a **verbatim quote**
   for every claim. Claims whose quote cannot be found in the source chunk are dropped — that is
   the hallucination guard. Nothing enters the bible without a passage behind it.
2. **Screen.** Cheap local heuristics over-generate candidate conflicts: same entity + same
   single-valued predicate + different value; a character who dies and then acts; conflicting
   years; name drift. No model calls.
3. **Adjudicate.** Each candidate goes to `muse-wonder-1.0` with the two quotes, everything else
   the bible knows about that entity, and — because Wonder has a 1,048,576-token window — the
   chapters involved and as much of the rest of the series as fits. The verdict is one of
   `contradiction` / `deliberate` (a reveal, a lie, a disguise, change over time) / `ambiguous` /
   `not_conflict` (the bible was wrong), with severity, an explanation, and a minimal fix to the
   *later* book. Earlier books are published; you never edit those.
4. **Report.** A single HTML page. Every finding opens to the lines that caused it.

The split is deliberate: Spark reads cheaply and widely; Wonder thinks expensively and only
where it matters. A six-book series costs a few dollars to audit end to end.

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env            # add MODEL_API_KEY from https://dev.meta.ai

canonkeeper fetch    evals/corpora/sherlock-holmes/series.json   # pulls the books from Project Gutenberg
canonkeeper estimate evals/corpora/sherlock-holmes/series.json   # tokens + dollars before you spend any
canonkeeper build    evals/corpora/sherlock-holmes/series.json   # extract the bible
canonkeeper audit    evals/corpora/sherlock-holmes/series.json   # find + adjudicate contradictions
```

Check your next manuscript against the bible without touching it:

```bash
canonkeeper check evals/corpora/sherlock-holmes/series.json drafts/book-07.md
```

No key? `--provider mock` runs the whole pipeline offline with a deterministic stand-in, which
is also what the test suite uses.

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
  audit/       candidate heuristics + adjudicator with 1M-ctx assembly (Muse Wonder)
  report/      HTML + JSON report
  eval/        defect injection + precision/recall scoring
  llm/         OpenAI-compatible client (Meta Model API / open-weights hosts / mock), disk cache, cost tally
  cli.py       fetch · estimate · build · audit · check · eval
evals/corpora/ public-domain series manifests used for benchmarking
docs/          ARCHITECTURE.md · DEMO_SCRIPT.md
```

## Models and providers

| role | default | why |
|---|---|---|
| extraction | `muse-spark-1.3` | cheap ($1.25/M in), 1M context, structured JSON out |
| adjudication | `muse-wonder-1.0` | frontier reasoning; 1M context lets it read both books before ruling |
| open-weights mode | `muse-spark-open` via Baseten / Modal / Fireworks / vLLM | `CANONKEEPER_PROVIDER=openweights` — same pipeline, your own hardware |
| reference cards (planned) | `muse-image-1.0` | render character/setting cards straight from the bible |

Everything is OpenAI-SDK compatible; set `CANONKEEPER_*` env vars or edit `.env`. Responses are
cached on disk by (model, prompt), so re-running an audit is free. Use Meta's **Standard** mode
(default) — Contributor mode trains on your prompts, and your manuscript is your manuscript.

## Status

Scaffold + working offline pipeline. Hackathon build runs **Oct 26 – Nov 4, 2026**. See
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) for the demo plan and the roadmap below for what lands when.

- [x] corpus loading, chapterizing, provenance offsets
- [x] claim extraction with quote grounding
- [x] bible store, alias resolution, slot index
- [x] candidate heuristics (slot mismatch, dead-speaks, timeline)
- [x] adjudicator with full-series context assembly
- [x] HTML/JSON report
- [x] seeded-defect eval harness
- [x] CLI, tests, CI
- [ ] prompt tuning on Sherlock Holmes; publish benchmark numbers
- [ ] `check` UX for a new manuscript (diff view against the bible)
- [ ] open-weights mode verified on a hosted Muse Spark Open endpoint
- [ ] Muse Image reference cards
- [ ] demo video

## License

MIT. Public-domain corpora are fetched from [Project Gutenberg](https://www.gutenberg.org/) at
run time and are not redistributed here.
