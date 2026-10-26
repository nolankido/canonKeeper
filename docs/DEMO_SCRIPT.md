# Demo script (target: 2:30–3:00 video + live-runnable)

Judging weights: Impact 30 · Use of Meta AI tooling 25 · Demo 25 · Technical depth 20.
Every beat below maps to one of those.

## 0:00 — The problem (Impact)
On camera or voiceover, one sentence: "I publish series fiction. Book six has to agree with book
one, written three years earlier, and the only tool for that is a spreadsheet and memory."
Show a one-star review complaining about a continuity slip. (Use a real but anonymised one, or
a paraphrase.)

## 0:20 — The famous one (Demo)
Terminal. `canonkeeper audit evals/corpora/sherlock-holmes/series.json` on the built bible with
cache warm, so it finishes in seconds. Open the report. The only contradiction, severity high:

> **John Watson · wound_location** — "struck on the shoulder by a Jezail bullet" (*A Study in
> Scarlet*) · "His left arm has been injured" (*A Study in Scarlet*) · "sat nursing my wounded
> leg" (*The Sign of the Four*) · "the jezail bullet which I had brought back in one of my
> limbs" (*The Noble Bachelor*). Fix: in *The Sign of the Four*, "wounded leg" → "wounded arm".

Say: "Readers have argued about this since 1890. Out of 2,331 facts and 26 suspects it found
exactly this one, and it quotes all four lines." Then scroll to the *deliberate* verdicts: Holmes's
dark eyes — "he is in disguise as an old master mariner; his true eye colour is grey." That is
the judgement a spreadsheet can't make.

## 0:55 — How it works (Meta tooling, Technical depth)
Architecture slide for 10 seconds: Spark extracts with mandatory quotes → local screen →
Wonder adjudicates with the whole series in context. Show the `estimate` output: "$2.10 to read
six novels, $X to judge." Then show one adjudication prompt in the cache folder with the token
count — make the 1M context visible, not just claimed.

## 1:25 — The author's workflow (Impact, Demo)
`canonkeeper check evals/corpora/sherlock-holmes/series.json evals/demo/holmes-book-07-draft.md`
— an original Holmes pastiche with three slips (Holmes's height, Mary Morstan's eyes,
Jefferson Hope alive) and one explained change (white hair = wig and powder for a disguise).
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
