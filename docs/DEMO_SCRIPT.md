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

> **John Watson's wound: shoulder / left arm in A Study in Scarlet, leg in The Sign of the
> Four** — "struck on the shoulder by a Jezail bullet" · "I had a Jezail bullet through it some
> time before". Fix, to the later book: "wounded leg" → "wounded arm".

Say: "Six novels, nearly four thousand sourced facts, and it reports the two errors readers have
argued about for a century — Watson's wandering wound, and the landlady who is Mrs. Hudson in
one story and Mrs. Turner in another — each with the passages on the page and the fix aimed at
the later book, because the earlier one is already in print." Scroll to *Reviewed and cleared*:
Holmes "dies" at the Reichenbach Falls and then walks through *The Hound*, and the report clears
it by quoting Watson: *The Hound* is told from the diary he kept at the time. "Every cleared
conflict quotes the passage that clears it. That judgement is the part a spreadsheet cannot
do." (If a re-run rules Holmes's disguised "dark eyes" correctly, show that instead.)

## 0:50 — Buying an audit (Design, PayPal + AI)
Browser, `canonkeeper serve` against the PayPal **sandbox**. Upload the three Saltmarsh books.
Quote page: price with the arithmetic shown — "$5 + $4 per 100k tokens; the token count is
known the moment the files land, so the price is known before anything runs."
Click **Pay with PayPal** → sandbox checkout → approve as the sandbox buyer → land on the job
page. Say over it: "Orders v2: create with PAY_NOW, the buyer approves, we capture on return,
and the capture has to be complete and for exactly the quoted amount before any model is
called. If an audit ever fails, one button refunds it through PayPal." Job page ticks to *done*; open the report:
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

## A draft cut, generated

`scripts/video/record.py` drives the running app in demo mode with Playwright and records each
beat above with captions drawn into the page; `scripts/video/assemble.py` trims, fades and joins
them into `build/video/preflight.mp4` (~2:15, silent, 1280×720). It is a starting point: replace
the simulated checkout with a screen recording of the real PayPal sandbox, add a voiceover if
you like, and keep it under three minutes.

```bash
canonkeeper serve --provider mock --port 8787 &
canonkeeper demo --dest build/demo
python scripts/mcp_flow_demo.py --brief --auto-approve --provider mock build/demo/books/*.txt > build/mcp.txt
python scripts/video/record.py --out build/video --mcp build/mcp.txt --draft build/demo/draft-04-the-ledger.txt
python scripts/video/assemble.py build/video
```

## Recording the real sandbox checkout (about 10 minutes, Windows)

The generated cut simulates the checkout; the video should show the real PayPal sandbox. Do
this once, on the laptop, in the repo folder:

1. **Prove the money path first.** `.venv\Scripts\canonkeeper doctor --round-trip`. It prints
   a sandbox approval link: open it, log in as the sandbox **personal (buyer)** account (from
   developer.paypal.com → Testing Tools → Sandbox Accounts → the `personal` account → View/edit
   account, which shows its email and password), approve. The terminal then shows capture →
   verify → refund. If any step is red, stop and send me the output.
2. **Warm the run.** `.venv\Scripts\canonkeeper serve` (sandbox credentials come from `.env`),
   open http://127.0.0.1:8787, press *Try it with the demo series*, pay with the sandbox buyer, wait for
   *done*. This fills the cache so the recorded run finishes in seconds. Leave the browser
   logged in to the sandbox buyer.
3. **Record.** Press `Win + Alt + R` (Xbox Game Bar starts recording the active window; the
   same keys stop it). Go to http://127.0.0.1:8787 → *Try it with the demo series* → *Pay with
   PayPal* → approve on the sandbox page → wait on the job page until *done* → *Open the
   report* → scroll to the first card. Stop recording. Aim for 30–40 seconds.
4. **Hand it back.** The clip lands in `Videos\Captures`. Copy it to
   `build\video\checkout.mp4` in the repo and tell me; I will cut it into the draft in place
   of the simulated checkout and keep the total under three minutes.
5. **Devpost testing notes.** If judges should try the hosted sandbox checkout, put the sandbox
   buyer's email and password in Devpost's private testing instructions, never in the repo.
