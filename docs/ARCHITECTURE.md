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

## Stage 0 — chapterizing

`corpus/chapterize.py`. Provenance points at chapters, and the adjudicator prioritises
chapters, so getting boundaries right matters. The six Holmes volumes on Gutenberg use five
different heading conventions (documented at the top of the module), every one of them has a
Contents block whose entries look like headings, and most have all-caps lines in the body.

Order of attack: (1) if a Contents block exists, its entries are ground truth and the body is
scanned for lines — or a numeral line plus the next line — that normalise to an entry;
(2) otherwise the strongest heading *family* present (`CHAPTER n` / `n. TITLE`) is used alone,
so bare sub-part numerals inside a story do not split it, and `PART` lines set a prefix rather
than a boundary; (3) otherwise fixed windows. Front matter before the first heading is not a
chapter, so a title page never becomes canon.

## Stage 1 — extraction (cheap model; `gemini-3.5-flash-lite` by default)

Per chapter (split at ~3.5k tokens on paragraph boundaries; two passes, a general one and a narrow sweep for descriptive facts). The prompt pins a predicate
vocabulary so the same fact lands in the same slot across books, and asks for normalised values.
A Flash-class model is used because extraction is wide and shallow: hundreds of calls, each
needing care but not deep reasoning. On the Gemini free tier that is $0 and about half an hour
at the paced rate; at list price a six-novel series is under a dollar.

Alias resolution happens at ingest: the extractor returns `{"Sherlock Holmes": ["Holmes", ...]}`
per chunk, the store merges them, and subjects are canonicalised (exact alias table, then fuzzy
WRatio ≥ 92 as a last resort). The rules that keep that honest, each found on the real Holmes
bible:

- pronouns and descriptions ("he", "my companion", "the landlady") are never aliases or subjects;
- fuzzy matching never crosses gendered honorifics or different given names (it once folded
  Mrs. Turner into Mr. Turner and erased the canon's best-known landlady slip);
- names that differ only by middle names/initials merge ("Enoch Drebber" → "Enoch J. Drebber");
- an alias two people claim ("Drebber": father and son) resolves to nobody;
- registration never overwrites an existing alias and resolution follows alias chains, and every
  build re-resolves all subjects against the final table, so the bible reloads into exactly the
  entities it was built with;
- `X died Y` where Y is a person or creature is stored as `killed` (models write it for shootings).

A build replaces the claims for the books it extracts rather than appending (claim ids hash the
normalised fact, so appending duplicated anything whose normalisation had changed).

## Stage 2 — candidate screening (no model)

`audit/candidates.py`. Deliberately high recall, low precision:

| heuristic | fires when |
|---|---|
| `slot_mismatch` | single-valued predicate, ≥ 2 distinct normalised values (substring and token-set ≥ 90 count as same) |
| `dead_speaks` | `dead`/`died` claim at position p, then a state/event claim at a later position |
| `timeline` | different four-digit years for `born` / `birth_year` / `died` |
| `name_drift` | first_name / surname / full_name differ for one entity (initials agree with full forms) |
| `origin_mismatch` | nationality and birthplace map to different countries |
| `role_collision` | two people hold a one-per-household role (landlady, butler) for the same household; lodging roles may tie to a protagonist when an anchor holds the role in 2+ chapters |

Values compare as places where they are places (a city agrees with its own region or country;
two different cities never agree), with number words folded ("six feet" = "6 feet"), and a hair
or eye "colour" with no colour word in it ("curly") is not compared at all.

Before screening, `canon/normalize.py` folds predicate synonyms (`eyes`, `eye_colour` →
`eye_color`) and value spellings (`gray` → `grey`, `30 years old` → `30`) at ingest, so the slot
index is the only join the screen needs. `max_candidates_per_run` caps spend.

## Stage 3 — adjudication (stronger model; `gemini-3.6-flash` free, `gemini-4-argon` paid)

Candidates are judged in batches (`adjudicate_batch_size`, default 8). Each call is three
messages, in this order for a reason:

1. **system** — the static instructions and the JSON contract (`{"verdicts": [{"id": …}]}`).
2. **user** — the series text, assembled greedily within `adjudicate_context_tokens`. When the
   whole series fits (the common case under the 1M window), this message is byte-identical
   across every batch, so an OpenAI-compatible host with prefix caching serves it at the cached
   rate. When it does not fit, the chapters the batch's quotes came from are selected first,
   then the rest in reading order; selected chapters are always emitted in reading order so the
   prefix stays as stable as possible.
3. **user** — the batch: for each candidate, its id, the claims with quotes, and every other
   fact the bible holds about that entity (so "she dyed her hair in chapter 4" is visible).

Verdicts are matched back by id; a missing entry is re-asked alone, and a malformed one
degrades to `ambiguous` rather than crashing. Fixes are always to the later passage.

**Clearing a conflict needs provenance too.** A `deliberate` or `not_conflict` verdict must quote
a passage that occurs in the series text the model was shown (never our own candidate list), and
a `deliberate` needs more than the conflicting quotes themselves — the passage where the story
explains the change. Otherwise the finding becomes `ambiguous` and the report lists it under
*Needs your judgement*. A 4B local model ruled planted slips "deliberate, likely character
development"; this rule is why that cannot quietly hide a real error.

Cost is input-dominated and the batch is the lever: reading a 900k-token series once per eight
candidates instead of once per candidate is an 8× cut before any cache discount. For iteration,
lower the context cap (`CANONKEEPER_ADJUDICATE_CONTEXT_TOKENS=150000`); for the final run, raise
it. Responses are cached on disk by prompt hash, so re-rendering is free.

## Evaluation

`eval/inject.py` plants sentences from per-predicate templates into later books and records
ground truth; `eval/score.py` matches contradiction-verdict findings to injections by
(subject fuzzy ≥ 85, predicate equal, value fuzzy ≥ 85). A clean baseline run supplies the keys
of pre-existing contradictions so they are not counted as false positives.

Known limitations of the harness: templates are stylistically uniform (a real slip is subtler);
injection into prose a model has memorised (Conan Doyle) may be *easier* to spot than in an
unknown manuscript. Both bias recall upward, so report numbers as an upper bound.

## The mock provider

`llm/rules.py` is a rule-based extractor and adjudicator that recognises ~20 sentence shapes
(the same shapes the eval harness's injection templates produce) and judges every candidate
with a fixed policy: a contradiction, unless the series text right after a quoted passage
carries an explicit explanation ("had been a trick of…", "had lied", "all along"), in which
case `deliberate`. That is a caricature of what the real adjudicator does with the whole chapter, and it is
enough for the demo's reveal case to show the verdict category exists. It exists so that the entire pipeline — build, bible, audit, check, eval,
report — runs deterministically with no key in CI, in tests, and in `canonkeeper demo`. It is
not a model and does not pretend to be; it is a harness for everything that is *not* the model.

## Providers and free-tier behaviour

Every provider is an OpenAI-compatible chat endpoint; `config.ProviderDefaults` holds the base
URL, default models and pacing per provider. The client (`llm/client.py`) is written for free
tiers: a sliding-window limiter keeps under the published RPM, 429/5xx responses are retried with
exponential backoff (Retry-After honoured), and if a server rejects `response_format` the client
remembers and stops sending it — `parse_json` already tolerates fenced or prose-wrapped JSON.

`CANONKEEPER_PROVIDER=openweights` points the same client at Ollama, LM Studio, vLLM or a hosted
open-weights endpoint. Adjudication with full-series context depends on the host's context
limit; the budget setting handles that. This is the mode for private manuscripts.

## What is intentionally not here

- A graph database. A few thousand facts in JSONL with an in-memory slot index is faster to
  build, trivially diffable, and hand-editable by an author.
- Embedding retrieval. Slots make conflicts a dictionary lookup; the adjudicator gets whole
  chapters rather than retrieved snippets, which is the point of the large context.
- A web app. The report is a static HTML file; the CLI is the product for now.
