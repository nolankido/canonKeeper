# Devpost submission draft — PayPal AI Hackathon 2026

Deadline: Thu Nov 12 2026, 12:00 PM PT (10:00 AM HST). Video < 3 min on YouTube, public repo
with LICENSE visible, functional demo (hosted URL or clear run instructions).

Judging criteria (unweighted): Technological Implementation · Design · Potential Impact ·
Innovation · Presentation. Special awards: Most Creative, Most Impactful, Best Demo Delivery,
Best Use of PayPal + AI, Best Use of Agentic Commerce ($5,000 each); overall $12k/$8k/$5k.

## Project name
Preflight — pay-per-audit continuity checking for series authors (built on canonkeeper)

## Tagline (≤ 80 chars)
Upload your series, get a quote, pay with PayPal, get every contradiction with its proof.

## Inspiration
I publish series fiction. Book six has to agree with book one, written three years earlier,
and the only tool for that is a spreadsheet and memory. Readers notice when it fails.

## What it does
**Book six has to agree with book one.** Preflight reads a whole series and shows every place it
doesn't: both passages, and a fix aimed at the later book (the earlier ones are in print).

- **A series bible with receipts.** Every fact about every character is tied to the exact
  sentence that states it. A model then rules on each fact that cannot all be true, with the
  series in context. Clearing a conflict (a reveal, a disguise, time passing) requires quoting
  the passage that explains it; otherwise it is a contradiction, or left to the author.
- **Paid per audit with PayPal.** The price is known before anything runs ($5 + $4 per 100k
  tokens, arithmetic shown). Nothing reaches a model until PayPal confirms the capture. A failed
  audit is refunded; a finding the author marks wrong returns $0.50 (up to half the price).
- **Next book?** Upload the draft: a smaller quote, the same checkout, and only the places the
  draft disagrees with what is published.
- **Agents as customers.** The same service is a hosted MCP server. An author's assistant
  quotes, orders and reads findings, or spends a budget the person funded once through PayPal,
  never past the balance or the person's limits. It never holds a PayPal credential and cannot
  approve a payment or claim a refund; the person does both. A real Claude session doing all of
  this on the sandbox is in the repo (`docs/agent-session/`).
- **What the author keeps:** the report (an AG Grid triage of every finding, then each finding
  with its passages), the series bible as an AG Grid explorer and a spreadsheet, the findings as
  JSON, and a read-only link to share with an editor.

## How we built it
Python. Extraction with a cheap long-context model under a strict schema with mandatory
verbatim quotes (claims whose quote isn't in the source are dropped). Local heuristics find
candidate conflicts for free. Batched adjudication sends the series text once per batch as a
stable prefix. PayPal Orders v2 in the sandbox: OAuth → create (PAY_NOW) → the buyer approves with PayPal's JS SDK
buttons (or PayPal's page) → capture → job runs in a background thread, persisted as JSON. A verified
PayPal webhook and a "check payment" button finish a payment whose buyer closed the tab; all three
routes go through one settle() that pays a job only on a completed capture of the quoted amount. A seeded-defect benchmark
injects contradictions into public-domain novels and scores precision/recall.

## Challenges
- Telling an error from a reveal needs the surrounding chapters, not just the two sentences.
- Project Gutenberg's five different heading conventions, each with a Contents block that
  looks like chapter headings.
- Free-tier rate limits: client-side pacing, backoff honouring Retry-After, JSON-mode fallback.

## Accomplishments
- On the six Holmes volumes (457,890 words) it extracted 3,963 sourced facts (99.5% of the
  model's claims grounded to a verbatim passage), screened 27 candidate conflicts, and found
  the canon's two best-known slips unassisted: Watson's war wound — "struck on the shoulder by
  a Jezail bullet" (*A Study in Scarlet*) vs "sat nursing my wounded leg" (*The Sign of the
  Four*) — and the landlady who is Mrs. Hudson in one story and Mrs. Turner in *A Scandal in
  Bohemia*. Fixes aimed at the later book. Free tier; about $1.55 at list price ($1.40 to read the books, $0.15 to judge).
- Cleared 18 candidates, each by quoting the passage that clears it — e.g. Holmes "dying" at
  the Reichenbach Falls and then acting in *The Hound* ("aided by the diary which I kept at the
  time": it is set earlier). Two of its four contradictions were wrong (a disguise; a maiden
  name), and the README says so rather than hiding it.
- Agentic commerce with the person in charge: an assistant connected to the hosted MCP endpoint
  opens a budget, the person approves it once on PayPal, and the assistant then buys audits and
  draft checks on its own, never past the balance; closing refunds the unused rest as a partial
  refund of the funding capture. An independent review of every money path (double spend,
  races, timeouts, webhook shapes) found six defects; each is now a regression test, and no
  lock is ever held across a PayPal call.
- Benchmark (seeded defects, 6 volumes, 20 planted contradictions): precision 0.80, recall
  0.40, F1 0.53 (0.64 / 0.35 / 0.45 as first scored; both kept, with the files to reproduce
  them). The first run scored 1.00 / 0.05; the harness pointed straight at extraction, and the
  per-stage breakdown still does — 8 of the 12 remaining misses are never extracted.

## What we learned
- Provenance is the product. Requiring a verbatim quote for every fact (and dropping the
  ~1% that cannot be located in the text) is what makes a finding trustworthy enough to pay
  for; the report shows passages, not opinions.
- The judgement is the hard part, and it needs context. "Dark eyes" vs "grey eyes" is an error
  until you read that Holmes is in disguise. A screen that over-generates and a judge that
  reads the surrounding chapters beats a clever screen on its own.
- Measure the pipeline, not the demo. The first benchmark run scored recall 0.05 and pointed
  at extraction, not judgement; a narrow second extraction pass and a higher reasoning budget, measured
  by the same harness, took recall from 0.05 to 0.35–0.40 at precision 0.64–0.80. The README table is generated, never typed.
- A free tier is a budget, not a limit. Daily caps (and the discovery that 503s count against
  them) turned into a client-side budget guard, a model fallback ladder, a cache that makes
  every run resumable, and `build --dry-run` that says exactly what a run will spend.
- Test where the users are. A line-ending change passed every Linux test and broke a draft
  check on Windows, the maintainer's own platform; CI now runs on both.
- An assistant needs a limit, not a key. Approving every payment makes the assistant a
  messenger; giving it credentials makes it a risk. A balance the person funds once through
  PayPal, spent under a lock and refunded on close, lets it act without ever holding a secret.
- Money code fails at the timeouts. Every refund is recorded with its amount and request id
  before PayPal is asked, so a retry after a lost answer sends the same refund, never a new one.
- The real PayPal sandbox differs from any fake: orders start `PAYER_ACTION_REQUIRED`, not
  `CREATED`; the create response omits the amount. Running `doctor --paypal` against the
  sandbox found two bugs the in-process fake had hidden.

## What's next
Names spoken in dialogue (Mary Watson calling her husband "James" is the one real Holmes slip
still missed) are built and opt-in until the benchmark measures them; then a standing series
bible that updates as each book is published, paid through PayPal Subscriptions.

## Built with
python, fastapi, paypal-orders-v2, paypal-js-sdk, paypal-webhooks, mcp, gemini-api, ag-grid, ollama, pydantic, jinja2, rapidfuzz, tiktoken, render

## Prizes to opt into
- **Best Use of Agentic Commerce:** the hosted MCP server (`/mcp`, 19 annotated tools; how to
  connect at `/assistant`), the PayPal-funded assistant budget, the person-only approval.
- **AG Grid:** two grids. The series bible explorer (`/sample/bible`: every fact by passage, or
  each fact by book with changes marked), which every finding in a report opens on its own row;
  and the report's triage grid of every finding (`/sample`), each row opening its card; on the
  author's own copy they rule on what the model left to them right in the grid.
- **Render:** the service runs on Render from `render.yaml` (a Blueprint: one click, three
  secrets), kept awake through judging, with a daily smoke test against it.

## PayPal tools used
PayPal REST Orders v2 (sandbox): create order with `payment_source.paypal.experience_context`
(`PAY_NOW`, return/cancel URLs), capture on return, idempotency via `PayPal-Request-Id`;
Payments v2 capture refunds (`/v2/payments/captures/{id}/refund`): full, when a paid audit
fails and the buyer chooses their money back over a free retry; partial, for the finding
guarantee and for the unused balance when an assistant budget is closed, each with its own
`PayPal-Request-Id` recorded before the call. The JS SDK buttons (PayPal, Venmo, Pay Later) on
the quote page, and webhooks (`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`) checked
with verify-webhook-signature.
Verified against the sandbox (`canonkeeper doctor --paypal`): orders come back
`PAYER_ACTION_REQUIRED` with a `payer-action` link; early captures answer `ORDER_NOT_APPROVED`
and are handled. Two surfaces: the web app (`canonkeeper serve`) and an MCP server
(hosted at `/mcp` over streamable HTTP, or `canonkeeper mcp` locally) through which an author's AI assistant quotes and orders the audit while
the person approves the payment on PayPal — agent-initiated, human-approved commerce.
`scripts/mcp_flow_demo.py` shows the whole exchange from the client side.

Nothing runs on money that has not arrived: a job becomes paid in exactly one function
(`payments.settle`), reached from PayPal's return redirect, from the MCP `confirm_payment` tool,
or from a "check payment" button for a buyer who approved and closed the tab. It requires a
completed capture (not a PENDING one) of exactly the quoted amount and currency, and the agent
never holds a credential that could approve. Model calls start only after that check passes.

Why the assistant budget captures the money and refunds the rest, instead of holding an
authorization: PayPal honours an authorization for only a few days, and a budget is meant to
last while the author writes the next book, weeks or months. Captured funds with every spend on
a receipt, a credit for a failed audit, and a partial refund of the remainder on close keep the
promise for as long as the author needs; Vault (saved payment methods charged later) needs
account approval a hackathon entry cannot get, and would let the merchant charge again without
the person in the loop.

Why plain Orders v2 and not PayPal's agent tooling: Preflight is the merchant. PayPal's agent
toolkit and MCP server let an assistant act on a PayPal account it is given; here the
assistant is a *customer* of a shop and must never hold PayPal credentials at all. So the shop
speaks Orders v2, Payments v2 refunds and verified webhooks itself, and the assistant only ever
hands the person PayPal's own approval page.

## AI tools used
Gemini API through its OpenAI-compatible endpoint: `gemini-3.5-flash-lite` for extraction
(two passes per chunk, medium reasoning effort) and `gemini-3.6-flash` for adjudication, both
on the free tier with a fallback ladder for overloaded models. Provider-agnostic: measured
locally on a laptop CPU with Ollama and `qwen3:4b` for private manuscripts (a sourced bible and
candidate list; its verdicts are not trusted, and the evidence rule sends them to the author).

Building it: the code, tests and documentation were written with Claude Code (Anthropic's coding
agent) working in this repository, directed and reviewed by the author; commits it made carry a
`Co-Authored-By` line. The prompts the product sends to Gemini, and every number in the README,
come from runs in this repository (the benchmark's files are committed).

## Image gallery (upload in this order; 3:2, 2250x1500)

`docs/gallery/01-report.png` … `05-job.png`, one claim per image with the screen that backs it
(the budget is the real Claude session's, `docs/agent-session/`):
the report's Watson finding, the quote with PayPal's buttons, the assistant budget (a real sandbox
funding, spend, credit and refund), the AG Grid bible, the receipt with a finding marked wrong.
Rebuild with `scripts/gallery.py` (see its docstring).

## Video beats (< 3:00)
See docs/DEMO_SCRIPT.md (v2): problem, audience and value in 15 seconds → the Holmes audit and
its bible explorer → buy an audit (real PayPal sandbox checkout) → an AI assistant, given a
PayPal-funded budget, checks the next draft on its own → the guarantee and the budget refund → benchmark →
close.

## Testing instructions (paste into Devpost; fill the two blanks, or drop that line)
**Hosted (fastest):** open `https://preflight-kq1o.onrender.com`. Press *Try it with the demo series* (an invented
three-book series with five planted errors and one change only the author can judge), then pay. Two ways, both PayPal's sandbox (test money, nothing real moves):
- **No login needed:** press PayPal's *Debit or Credit Card* button and pay as a guest with
  PayPal's published sandbox test card `4012 0000 3333 0026`, any future expiry, any 3-digit CVV,
  any US name and address. Switch off *Save info & create your PayPal account* and press *Pay Now*.
- **Or** *Pay with PayPal* as the sandbox buyer `<sandbox buyer email>` / `<password>`.

 You return to the job page, which
updates itself; the audit takes about a minute (instant if someone ran it before you: demo-series
responses are shared). Open the report. *See a finished audit* on the home page opens the real
six-volume Sherlock Holmes audit without paying.

**As an AI assistant's customer:** in Claude Desktop, Settings → Connectors → Add custom
connector → `https://preflight-kq1o.onrender.com/mcp`. Ask: "Open a $20 Preflight budget." Approve the link it gives
you with the sandbox buyer, then: "Audit the Saltmarsh demo series and pay from the budget."
When it is done: "Check the demo series' next draft against it." Finally: "Close the budget": the
unused balance goes back to PayPal. The page `/assistant` on the site has the address, these prompts, what
the assistant can and cannot do, and its tool list.

The hosted instance runs on Gemini's free tier, which may use submitted text for training:
please upload only public-domain text or the demo series.

**Local, no keys at all (2 minutes):**
```bash
git clone https://github.com/nolankido/canonKeeper && cd canonKeeper
pip install -e ".[dev]"
canonkeeper demo --dest build/demo --open-report   # whole pipeline offline, rule-based model
canonkeeper serve --provider mock       # Preflight with simulated PayPal: http://127.0.0.1:8787
# (the offline stand-in reads the demo series' sentence shapes; real books need a model key)
# in a second terminal, while serve runs: the same flow driven by an assistant over MCP
python scripts/mcp_flow_demo.py --brief --auto-approve --provider mock build/demo/books/*.txt
```
With `PAYPAL_CLIENT_ID` /
`PAYPAL_CLIENT_SECRET` (sandbox) in `.env`, `serve` uses the real sandbox, and
`canonkeeper doctor --round-trip` proves create → approve → capture → refund against it.
