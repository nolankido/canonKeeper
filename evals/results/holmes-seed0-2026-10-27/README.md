# Holmes benchmark, seed 0 (2026-10-27)

Six public-domain Holmes volumes; 20 contradictions planted in later books by `canonkeeper eval
--n 20 --seed 0`; the whole pipeline (extraction, screen, judgement) re-run on the mutated text.

- `injections.json` — what was planted, where, and the exact sentence.
- `report.json` — the audit of the mutated series. `baseline.json` — the audit of the clean
  series (pre-existing contradictions are excluded from false positives).
- `result.raw.md` — the score as the run wrote it. `result.md` — rescored with the scorer in
  this commit (surname-compatible subjects; a contradiction that quotes a planted sentence for
  another reason is listed apart instead of as a false positive). Reproduce it with no model calls:

```bash
canonkeeper rescore evals/results/holmes-seed0-2026-10-27
```

`result.md` also lists each miss by the stage that lost it. That table needs the mutated
series' `bible.jsonl` (1.7 MB, not committed); copy it into the folder from
`.canonkeeper/sherlock-holmes/reports/eval-seed0/` to regenerate it. Without it, `rescore`
writes the score alone.

Judging: 4 batches on `gemini-3.6-flash`, then 4 on `gemini-3.5-flash-lite` after the free-tier
fallback (3.6/3.8 Flash were overloaded or out of their 20 requests/day). The per-stage miss
breakdown is in `result.md`: 8 of the 12 misses were lost at extraction, 4 at judgement.

Known benchmark-quality issues in this run, fixed for later runs: three plants were not clear
contradictions ("thumb" vs "hand", "dark" vs "brown" eyes, a birthplace for "Andaman
Islanders"), and one was planted against a bible fact that was itself a misreading (Watson
"born in Afghanistan").
