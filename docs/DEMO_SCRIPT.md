# Demo script — Preflight (PayPal AI Hackathon 2026)

Target 2:50, hard limit 3:00, public on YouTube. Record by **Nov 9**; submit by **Nov 12, 12:00 PT**.

Judging criteria (unweighted): Technological Implementation · Design · Potential Impact ·
Innovation · Presentation. Special award we are aiming at: **Best Use of Agentic Commerce** (with the AG Grid sponsor
prize for the bible explorer). Every beat below names what it is for.

The video must show the project working end-to-end, on the machine it was built for, with no
third-party music or copyrighted material. Everything shown is public-domain text or our own
invented series. Warm every cache first: nothing on screen should wait on a model.

Script v3 (2026-10-27). Narration is timed at 150 words a minute; each beat lists its word
count. Numbers on screen are real: read them off the pages on the day, and if a benchmark run has
added a newer README row, read that row instead. v3 replaces the scripted assistant with a real
recorded agent session, and adds the triage grid and the bible's chart to the Holmes beat.

## 0:00–0:06 — Problem, audience, value (Impact) · 30 words
On screen: the opening card.
> "Book six has to agree with book one. Preflight reads a whole series and shows every
> contradiction, with both passages and a fix for the newer book."

## 0:06–0:50 — It works on a real series (Technological Implementation, Design) · 92 words
The Holmes sample report: the triage grid (AG Grid) → click Watson's row → his card → the
landlady → the Reichenbach clearance → *see it in the bible* (AG Grid, one column per book) →
click the *Return* bar of the chart (AG Charts) to filter to the facts that book changes.
> "Here it is on all six Sherlock Holmes books. Every finding sits in one grid, with my own
> rulings beside the model's. It finds the slips readers have argued about for a century:
> Watson's wound moves from his shoulder to his leg, and the landlady is Mrs. Hudson, then Mrs.
> Turner. When Holmes dies and comes back, it clears that, and quotes the passage that explains
> it. Underneath is a bible of 3,963 facts, each tied to its sentence: click a book, and you see
> what it changed."

## 0:50–1:30 — Buying an audit (PayPal, Design) · 66 words
Home → *Try it with the demo series* → quote page → PayPal's card button, as a guest with
PayPal's sandbox test card (the pop-up plays at 1.7×) → job page ticks to *done* → the report.
> "Buying one: the price is known before anything runs, five dollars plus four per hundred
> thousand tokens, with the arithmetic on the page. Pay with PayPal. Nothing touches a model
> until PayPal confirms the capture of exactly that amount, and a failed audit refunds with one
> button. A minute later: five planted contradictions, each with its two passages."

## 1:30–2:30 — A real assistant with a budget (Agentic Commerce, Innovation) · 112 words
`agent-video.webm` from `scripts/video/agent_session.py`: a real headless agent connected to
Preflight's MCP endpoint, every call and reply its own, replayed in an unbranded chat panel
("Your AI assistant": the rules bar third-party trademarks in the video). It ends on the
budget's page: every spend, the refund for the finding marked wrong, the rest refunded.
> "The same shop serves AI assistants over MCP, and this is a real session. I ask for a
> twenty-dollar budget, at most six dollars a purchase. The assistant opens the order, but only I
> can approve it, on PayPal. From then on it works on its own: it audits the series, pays from the
> budget, and brings back the contradictions. When I say one is wrong, it can't refund itself: it
> gives me a link, and I confirm it, fifty cents back. It checks my next draft, then closes the
> budget, and the rest goes back to PayPal. I set the limit once; it acts inside it, and never
> holds a PayPal credential."

## 2:30–2:45 — Is it honest? (Technological Implementation) · 38 words
README benchmark table.
> "We measure it: contradictions are planted in the Holmes novels and the pipeline is scored on
> what it finds. Precision 0.80, recall 0.40 on the last run. The misses are listed, and so is
> the work on them."

## 2:45–2:52 — Close · 20 words
Repo URL and hosted URL on screen.
> "Open source, MIT, free tier or your own machine for private books. Preflight: pay for the
> audit, get the proof."

## Production notes
- Caches warm: `audit`, `check` and the Saltmarsh job must complete in seconds on camera. Run
  each once the day before; a re-run replays from `.canonkeeper/cache`.
- No PayPal login on camera: checkout is PayPal's guest card form with the sandbox test card.
- The assistant beat is the real session's film: no branded assistant UI on screen.
- Never show `.env`, a real manuscript, a pen name, or anything under `drafts/`.
- Keep the PayPal checkout on screen only as long as the approval takes; the product is the
  report, not the payment page.

## The cut, generated

`scripts/video/agent_session.py` runs the assistant beat for real: a headless agent connected
to the server's MCP endpoint, asked five things an author would ask. The one step it can never
take, approving a PayPal payment, the script takes as the person would. It saves the transcript
and films it twice: `agent.webm` (named, for /assistant and the README) and `agent-video.webm`
(the generic title and shorter holds the video needs).

`scripts/video/record.py` films every other beat from the running app with captions drawn into
the page, and takes `--agent-clip` for the assistant beat. Against a server with PayPal
**sandbox** keys the payment is real: the recorder pays through PayPal's own card button as a
guest with PayPal's published sandbox test card (no PayPal login, local server only), films the
pop-up and splices it in where it opened; the cut plays it at 1.7×. Without sandbox keys the
same scenes run on the simulated checkout. `scripts/video/assemble.py` fades and joins the
scenes into `preflight.mp4` (1280×720, silent; narration is added over it) and warns at 3:00.

```bash
set -a; . keys.env; set +a           # sandbox keys only
canonkeeper serve --provider mock --port 8791 --workdir build/w &
python scripts/video/agent_session.py --base http://127.0.0.1:8791 --out build/agent
python scripts/video/record.py --base http://127.0.0.1:8791 --out build/video \
    --agent-clip build/agent/agent-video.webm
python scripts/video/assemble.py build/video
```

Devpost testing notes: if judges should try the hosted sandbox checkout, put the sandbox buyer's
email and password in Devpost's private testing instructions, never in the repo.
