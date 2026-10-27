# Demo script — Preflight (PayPal AI Hackathon 2026)

Target 2:40, hard limit 3:00, public on YouTube. Record by **Nov 9**; submit by **Nov 12, 12:00 PT**.

Judging criteria (unweighted): Technological Implementation · Design · Potential Impact ·
Innovation · Presentation. Special awards we are aiming at: **Best Use of PayPal + AI**,
**Best Use of Agentic Commerce**, **Best Demo Delivery**. Every beat below names what it is for.

The video must show the project working end-to-end, on the machine it was built for, with no
third-party music or copyrighted material. Everything shown is public-domain text or our own
invented series. Warm every cache first: nothing on screen should wait on a model.

## 0:00 — The problem (Impact)
Voiceover, one sentence: "I publish series fiction. Book six has to agree with book one,
written three years earlier, and the only tool for that is a spreadsheet and memory."
Paraphrase (do not screenshot) a reader complaint about a continuity slip.

## 0:15 — What it finds (Impact, Technological Implementation)
Open the finished Holmes report (`docs/img/report.png` is the frame). Read the finding:

> **John Watson · wound_location** — "struck on the shoulder by a Jezail bullet" (*A Study in
> Scarlet*) · "sat nursing my wounded leg" (*The Sign of the Four*). Fix, to the later book:
> "wounded leg" → "wounded arm".

Say: "Six novels, 2,331 sourced facts, and it reports the one error readers have argued about
since 1890 — with the four passages on the page and the fix aimed at the later book, because
the earlier one is already in print." Scroll to a *deliberate* verdict (Holmes's dark eyes:
"he is in disguise"). "That judgement is the part a spreadsheet cannot do." If the resumed run
confirms it, add the landlady: "It also noticed Mrs. Hudson became Mrs. Turner for one story."

## 0:50 — Buying an audit (Design, PayPal + AI)
Browser, `canonkeeper serve` against the PayPal **sandbox**. Upload the three Saltmarsh books.
Quote page: price with the arithmetic shown — "$5 + $4 per 100k tokens; the token count is
known the moment the files land, so the price is known before anything runs."
Click **Pay with PayPal** → sandbox checkout → approve as the sandbox buyer → land on the job
page. Say over it: "Orders v2: create with PAY_NOW, the buyer approves, we capture on return.
No model is called until the capture succeeds." Job page ticks to *done*; open the report:
five planted contradictions, each with its two quotes.

## 1:35 — The same thing through an assistant (Agentic Commerce)
Terminal, `python scripts/mcp_flow_demo.py books/*.txt` (server already running). Show the
tool calls scrolling: `quote_series` → `order_audit` → `confirm_payment` **refuses** ("not
approved yet") → the approval link is printed for the person → approve in the browser →
`confirm_payment` → `audit_status` → `get_findings` as data. Say: "The agent prices the job and
creates the order. It never holds a credential and cannot approve the payment; the approval
goes to the person. That is the shape agentic commerce should have." Mention it is a standard
MCP server — any assistant that speaks MCP can drive it.

## 2:10 — The next book (Impact)
`canonkeeper check … evals/demo/holmes-book-07-draft.md`: an original pastiche with three
planted slips and one explained change (white hair = wig and powder). Two contradictions with
fixes to the new draft; the disguise classified *deliberate*. "This is the author's loop:
every new manuscript is checked against everything already published."

## 2:30 — Is it honest? (Technological Implementation)
README benchmark table for five seconds. "We seed contradictions into public-domain novels and
score precision and recall; the numbers in the README come from that command, not from us."
Read the current row, including what it misses.

## 2:45 — Close
"Open source, MIT. Runs on a free tier with no card, or on your own machine for private
manuscripts. Preflight: pay for the audit, get the proof." Repo URL on screen.

## Production notes
- Caches warm: `audit`, `check` and the Saltmarsh job must complete in seconds on camera. Run
  each once the day before; a re-run replays from `.canonkeeper/cache`.
- Sandbox buyer: log in once in the recording browser before starting so checkout is two clicks.
- Terminal font ≥ 18pt; report pages at 110% zoom; 1920×1080.
- Never show `.env`, a real manuscript, a pen name, or anything under `drafts/`.
- Keep the PayPal checkout on screen only as long as the approval takes; the product is the
  report, not the payment page.
