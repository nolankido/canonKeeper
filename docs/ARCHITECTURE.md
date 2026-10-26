# Architecture

## The problem, precisely

A series bible is a set of facts, each true at some point in story time, each asserted by some
passage. A continuity error is a set of passages that cannot all be true under any reading the
author intended. The hard part is not finding disagreements — that is string comparison — it is
telling an *error* from a *reveal*, a *lie*, a *disguise*, or a *change over time*. That
judgement needs the surrounding text, often from two different books. That is why the design
has two model roles with very different budgets.

## Data model

```
Claim { kind, subject, predicate, object, story_time?, confidence, provenance }
Provenance { book, book_index, chapter, chapter_index, quote, char_start, char_end }
Candidate { claims[2+], reason, heuristic }
Finding { candidate, verdict, severity, explanation, suggested_fix, adjudicator_model }
```

- A claim's **slot** is `(subject, predicate)`. Two claims in one slot with different objects are
  a candidate conflict only if the predicate is single-valued (`eye_color`, yes; `location`, no).
- **Provenance is mandatory and verified.** The extractor searches the source chunk for the quote
  (exact, then fuzzy ≥ 90) and records the character span. Quotes that are not found are dropped
  and counted in `ExtractStats.dropped_ungrounded`. This is the only hallucination control and it
  is cheap, so it is strict.
- `story_time` is free text. It is passed through to the adjudicator rather than parsed, except
  for four-digit years in the `timeline` heuristic.

## Stage 1 — extraction (Muse Spark, `muse-spark-1.3`)

Per chapter (split at ~6k tokens on paragraph boundaries). The prompt pins a predicate
vocabulary so the same fact lands in the same slot across books, and asks for normalised values.
Spark is used because extraction is wide and shallow: thousands of calls, each needing care but
not deep reasoning, at $1.25/M input. A six-novel series is roughly 1M tokens → about $2.

Alias resolution happens at ingest: the extractor returns `{"Sherlock Holmes": ["Holmes", ...]}`
per chunk, the store merges them, and subjects are canonicalised (exact alias table, then fuzzy
WRatio ≥ 92 as a last resort). Mis-merges are a known failure mode and show up as
`not_conflict` verdicts, which the eval harness surfaces.

## Stage 2 — candidate screening (no model)

`audit/candidates.py`. Deliberately high recall, low precision:

| heuristic | fires when |
|---|---|
| `slot_mismatch` | single-valued predicate, ≥ 2 distinct normalised values (substring and token-set ≥ 90 count as same) |
| `dead_speaks` | `dead`/`died` claim at position p, then a state/event claim at a later position |
| `timeline` | different four-digit years for `born` / `birth_year` / `died` |
| (planned) `name_drift` | first_name / surname differ for one entity |

Every candidate costs one adjudicator call, so `max_candidates_per_run` caps spend.

## Stage 3 — adjudication (Muse Wonder, `muse-wonder-1.0`)

For each candidate, the prompt contains: the claims with quotes; every other fact the bible holds
about the entity (so "she dyed her hair in chapter 4" is visible); then source text, assembled
greedily within `adjudicate_context_tokens` (default 900k): first the chapters the quotes came
from, then the rest of the series in order until the budget is hit. On a typical series the whole
thing fits — this is the capability the 1M context unlocks, and the reason the adjudicator can
distinguish a planted twist from a slip.

Verdicts are a closed set; anything unparseable degrades to `ambiguous` rather than crashing.
Fixes are always to the later passage.

Cost: input-dominated. ~$4 per full-context verdict at $4/M. A run with 30 candidates on a
900k-token series is ~$100 — too much for a hackathon credit budget, so the default context cap
should be lowered (`CANONKEEPER_ADJUDICATE_CONTEXT_TOKENS=150000`) for iteration and raised for
the final demo run. Responses are cached on disk by prompt hash, so re-rendering is free.

## Evaluation

`eval/inject.py` plants sentences from per-predicate templates into later books and records
ground truth; `eval/score.py` matches contradiction-verdict findings to injections by
(subject fuzzy ≥ 85, predicate equal, value fuzzy ≥ 85). A clean baseline run supplies the keys
of pre-existing contradictions so they are not counted as false positives.

Known limitations of the harness: templates are stylistically uniform (a real slip is subtler);
injection into prose a model has memorised (Conan Doyle) may be *easier* to spot than in an
unknown manuscript. Both bias recall upward, so report numbers as an upper bound.

## Open-weights mode

`CANONKEEPER_PROVIDER=openweights` points the same client at any OpenAI-compatible host serving
Muse Spark Open or Muse Glimmer. Extraction works unchanged. Adjudication with full-series
context depends on the host's context limit; the budget setting handles that.

## What is intentionally not here

- A graph database. A few thousand facts in JSONL with an in-memory slot index is faster to
  build, trivially diffable, and hand-editable by an author.
- Embedding retrieval. Slots make conflicts a dictionary lookup; the adjudicator gets whole
  chapters rather than retrieved snippets, which is the point of the large context.
- A web app. The report is a static HTML file; the CLI is the product for now.
