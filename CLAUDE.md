# canonkeeper — agent working notes

Hackathon project (Meta Global AI Developer Hackathon, Indie track, Build for Your World).
Submissions due **Wed Nov 4 2026, 9:00 PM ET** (4:00 PM Hawaii). Record the demo video by Nov 2.

## What this is

Series-continuity engine. Pipeline: corpus → chapterize → extract claims (Muse Spark) → JSONL
bible → local candidate heuristics → adjudicate (Muse Wonder, up to ~900k tokens of context) →
HTML report. `README.md` has the overview; `docs/ARCHITECTURE.md` has the reasoning.

## Non-negotiables

- **Provenance or nothing.** A claim without a verbatim, grounded quote is dropped, never kept.
  Do not weaken `_ground()` in `extract/extractor.py` to raise recall.
- **Fix later books, never earlier ones.** Earlier books are published. Suggested fixes always
  target the later passage.
- **Public-domain corpora only** in this repo and in any demo material (`evals/corpora/`).
  Never add real author manuscripts, private drafts, or anything from the maintainer's own
  catalog. `drafts/` is git-ignored for local use only.
- **Standard mode, not Contributor mode** on Meta Model API. Contributor mode trains on prompts.
- **Keep the mock provider working.** CI and tests run offline via `MockLLM`. Any change to the
  extraction or adjudication contract must keep `tests/` green with no API key.
- **Measure, don't eyeball.** Prompt changes are evaluated with `canonkeeper eval`, and the
  README benchmark table is updated from `result.md`, not typed by hand.

## Commands

```bash
pip install -e ".[dev]"
pytest                      # offline, mock provider
ruff check src tests && ruff format src tests && mypy
canonkeeper demo            # offline, end to end
canonkeeper fetch|estimate|build|bible|audit|check|eval evals/corpora/sherlock-holmes/series.json
```

## Layout

- `src/canonkeeper/config.py` — all settings; model IDs live here and in `.env.example`.
- `src/canonkeeper/llm/client.py` — OpenAI-compatible client, disk cache, cost tally (`PRICING`).
- `src/canonkeeper/extract/` — prompts + extractor. Predicate vocabulary is in `prompts.py`;
  keep it in sync with `audit/candidates.py::SINGLE_VALUED`.
- `src/canonkeeper/audit/candidates.py` — cheap heuristics; over-generate, let Wonder decide.
- `src/canonkeeper/audit/adjudicate.py` — batched adjudication; series text is user message #1
  (stable, cacheable), candidates are message #2. Keep that order.
- `src/canonkeeper/canon/normalize.py` — predicate/value synonym tables applied at ingest.
- `src/canonkeeper/llm/rules.py` — the rule-based mock. When adding an injection template to
  `eval/inject.py::TEMPLATES`, add a matching pattern here so the eval test keeps recall 1.0.
- `src/canonkeeper/demo.py` — the invented Saltmarsh series; `canonkeeper demo` must keep
  finding all five planted errors (tested).
- `src/canonkeeper/eval/` — defect injection (`TEMPLATES`) and scoring.
- `tests/conftest.py` — fixture series with planted defects; imports the mock from `llm/rules.py`.

## Conventions

- Python 3.11+, ruff (line length 100), mypy clean, pydantic v2.
- Small focused commits with conventional prefixes (`feat:`, `fix:`, `eval:`, `docs:`).
- Model IDs: `muse-spark-1.3`, `muse-wonder-1.0`, `muse-image-1.0`, `sam-3.1`
  (per https://dev.meta.ai/docs/overview, 2026-10-26). Verify before changing.
