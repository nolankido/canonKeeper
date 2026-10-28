# Demo script — Preflight (PayPal AI Hackathon 2026)

Target 2:50, hard limit 3:00, public on YouTube. Record by **Nov 9**; submit by **Nov 12, 12:00 PT**.

Judging criteria (unweighted): Technological Implementation · Design · Potential Impact ·
Innovation · Presentation. Special award we are aiming at: **Best Use of Agentic Commerce** (with the AG Grid sponsor
prize for the bible explorer). Every beat below names what it is for.

The video must show the project working end-to-end, on the machine it was built for, with no
third-party music or copyrighted material. Everything shown is public-domain text or our own
invented series. Warm every cache first: nothing on screen should wait on a model.

Script v2 (T17, 2026-10-27). Narration is timed at 150 words a minute; each beat lists its
word count, and the total is 363 words, 2:25 of speech inside a 2:50 cut (room for the screens to breathe). Numbers on screen are real:
read them off the hosted pages on the day, and if a benchmark run has added a newer README row,
read that row instead.

## 0:00–0:15 — Problem, audience, value (Impact) · 36 words
On screen: the Holmes report, first card (Watson's wound, both passages, the fix).
> "Book six has to agree with book one. Preflight reads a whole series and shows every
> contradiction: both passages, and a fix for the newer book. For series authors and editors,
> paid per audit with PayPal."

## 0:15–0:45 — It works on a real series (Technological Implementation) · 68 words
Scroll the Holmes sample report, then open *Explore the bible* (AG Grid) and tick *Disputed
facts only*.
> "Here it is on all six Sherlock Holmes books. It finds the two slips readers have argued about
> for a century: Watson's wound moves from his shoulder to his leg, and the landlady is Mrs.
> Hudson, then Mrs. Turner. When Holmes dies and comes back, it clears that, and quotes the
> passage that explains it. Underneath is a bible of 3,963 facts, each one tied to its
> sentence."

## 0:45–1:20 — Buying an audit (PayPal + AI, Design) · 72 words
Hosted URL. *Try it with the demo series* → quote page → the gold PayPal button → sandbox
checkout → approve → job page ticks to *done* → *Open the report*.
> "Buying one: upload the books and the price is known before anything runs: five dollars, plus
> four per hundred thousand tokens, with the arithmetic on the page. Pay with PayPal. Nothing
> touches a model until PayPal confirms a completed capture of exactly the quoted amount, and a
> failed audit refunds through PayPal with one button. A minute later: the report, five planted
> contradictions in the demo series, each with its two passages."

## 1:20–2:05 — An assistant with a budget (Agentic Commerce) · 92 words
Claude Desktop with the hosted connector (`<url>/mcp`). Type: *"Open a $20 Preflight budget."*
Claude replies with an approval link; click it, approve on the sandbox PayPal page (Claude
cannot). Then: *"Check my new draft of book four against the series."* Claude sends an upload
link, the draft is uploaded, it quotes, pays from the budget and reads the findings back.
Cut to the budget page: the spend, the balance.
> "The same shop serves AI assistants over MCP. I give Claude a twenty-dollar budget. It can
> open the order, but only I can approve it, on PayPal. From then on it works on its own:
> it checks my next draft against everything already published, pays from the budget, and
> brings back the contradictions. It can never spend past the balance, every spend is on a
> receipt, and it never holds a PayPal credential. That is the shape agentic commerce should
> have: the person sets the limit once, the assistant acts inside it."

## 2:05–2:25 — The author stays in charge (Innovation) · 37 words
Job page: *This one is wrong* on a finding → receipt shows $0.50 back. *Needs your judgement*:
*Not a problem*. Budget page: *Close the budget* → *Refunded to PayPal*.
> "If a finding is wrong, I say so: fifty cents comes back through PayPal, up to half the
> price, and the report shows my call beside the model's. When I'm done, closing the budget
> refunds the rest."

## 2:25–2:40 — Is it honest? (Technological Implementation) · 38 words
README benchmark table.
> "We measure it: contradictions are planted in the Holmes novels and the pipeline is scored on
> what it finds. Precision 0.80, recall 0.40 on the last run. The misses are listed, and so is
> the work on them."

## 2:40–2:50 — Close · 20 words
Repo URL and hosted URL on screen.
> "Open source, MIT, free tier or your own machine for private books. Preflight: pay for the
> audit, get the proof."

## Production notes
- Caches warm: `audit`, `check` and the Saltmarsh job must complete in seconds on camera. Run
  each once the day before; a re-run replays from `.canonkeeper/cache`.
- Sandbox buyer: log in once in the recording browser before starting so checkout is two clicks.
- Claude Desktop: add the connector before recording (Settings → Connectors → Add custom
  connector → `<hosted url>/mcp`), and run the budget beat once off camera so the draft check
  is cached. Show the approval click on PayPal: it is the point of the beat.
- Terminal font ≥ 18pt; report pages at 110% zoom; 1920×1080.
- Never show `.env`, a real manuscript, a pen name, or anything under `drafts/`.
- Keep the PayPal checkout on screen only as long as the approval takes; the product is the
  report, not the payment page.

## A draft cut, generated

`scripts/video/record.py` drives the running app in demo mode with Playwright and films each
beat of v2 with captions drawn into the page, including the assistant scene: it calls the
server's own MCP endpoint the way Claude Desktop would (open a budget, check the draft, pay from
the budget, read the findings) and films the person's side, the PayPal approval and the budget
page. `scripts/video/assemble.py` trims, fades and joins the scenes into
`build/video/preflight.mp4` (about 2:50, silent, 1280×720).

Against a server with PayPal **sandbox** keys, both payments are real: the recorder pays through
PayPal's own card button as a guest with PayPal's published sandbox test card (no PayPal login),
films PayPal's pop-up full width and splices it in where it opened, and the cut plays the form
fills at 1.7×. The assistant panel is labelled generically ("Your AI assistant"), not with any
product's name, because the rules bar third-party trademarks in the video. Without sandbox keys
the same scenes run on the simulated checkout.

```bash
canonkeeper serve --provider mock --port 8787 &
canonkeeper demo --dest build/demo
python scripts/video/record.py --out build/video --draft build/demo/draft-04-the-ledger.txt
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
   of the simulated checkout and keep the total under three minutes. The cut is one command,
   which scales any recording size to 1280x720 and warns if the total reaches 3:00:
   `python scripts/video/assemble.py build/video --replace buy=build/video/checkout.mp4`.
5. **Devpost testing notes.** If judges should try the hosted sandbox checkout, put the sandbox
   buyer's email and password in Devpost's private testing instructions, never in the repo.
