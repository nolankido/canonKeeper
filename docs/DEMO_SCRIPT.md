# Demo script (target: 2:30–3:00 video + live-runnable)

Judging weights: Impact 30 · Use of Meta AI tooling 25 · Demo 25 · Technical depth 20.
Every beat below maps to one of those.

## 0:00 — The problem (Impact)
On camera or voiceover, one sentence: "I publish series fiction. Book six has to agree with book
one, written three years earlier, and the only tool for that is a spreadsheet and memory."
Show a one-star review complaining about a continuity slip. (Use a real but anonymised one, or
a paraphrase.)

## 0:20 — The famous one (Demo)
Terminal. `canonkeeper audit evals/corpora/sherlock-holmes/series.json` on a pre-built bible with
cache warm, so it finishes in seconds. Open the report. First finding:

> **Watson · wound_location** — "shoulder" (*A Study in Scarlet*, Part I, Ch. I) vs "leg"
> (*The Sign of the Four*, Ch. I). Verdict: contradiction, high.

Say: "Readers have argued about this since 1890. It found it in under a minute, and it quotes
both lines." Scroll to the John/James Watson finding and the Mrs. Hudson/Mrs. Turner finding.

## 0:55 — How it works (Meta tooling, Technical depth)
Architecture slide for 10 seconds: Spark extracts with mandatory quotes → local screen →
Wonder adjudicates with the whole series in context. Show the `estimate` output: "$2.10 to read
six novels, $X to judge." Then show one adjudication prompt in the cache folder with the token
count — make the 1M context visible, not just claimed.

## 1:25 — The author's workflow (Impact, Demo)
`canonkeeper check` on an unpublished draft. The Saltmarsh demo already ships one
(`draft-04-the-ledger.txt`: an eye-colour slip, a birthplace slip, and a *deliberate* reveal —
a character's grey eyes were tinted glasses, explained in the text). For the video, prefer a
Holmes "book seven" draft with the same three shapes so the whole demo stays on one corpus.
Report shows two contradictions with suggested fixes **to the new book**, and the reveal
classified as `deliberate` with Wonder's explanation. That classification is the whole point;
linger on it.

## 1:55 — Does it work? (Technical depth)
Show the README benchmark table and the `eval` command. One sentence on seeded-defect injection
and the baseline exclusion. If numbers are good, read them; if mixed, say what was missed and
why — honesty reads better than polish to technical judges.

## 2:20 — Bonus beats (Meta tooling)
- Toggle `--provider openweights` and run `build` on one chapter against a hosted Muse Spark
  Open endpoint: same output, your own hardware.
- If built: character reference cards from the bible via Muse Image.

## 2:40 — Close
"Open source, MIT, works on your manuscript today. Built in ten days with Meta's models."
Repo URL on screen.

## Production notes
- Record by **Nov 2**. Two days of slack for a re-take.
- Warm every cache before recording; nothing live should take more than 10 seconds on screen.
- Terminal font ≥ 18pt, light theme for the report, dark for the terminal.
- Do not show any real manuscript, pen name, or private file. `drafts/` is git-ignored; the fake
  book seven should be written for the demo and committed under `evals/demo/` if it is clean.
