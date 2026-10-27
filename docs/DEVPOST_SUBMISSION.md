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
Reads an entire book series, builds a series bible where every fact carries the exact passage
that asserted it, screens for facts that cannot all be true, and has a model rule on each one
with the series text in context: a real contradiction (with a fix aimed at the later book), a
deliberate reveal, or noise. A ruling that clears a conflict must quote the passage that
clears it, or it goes to the author as "needs your judgement". The audit's price is known
before it runs — the token count is — so Preflight quotes it, takes payment through PayPal,
and only then spends any compute.

When the author is writing the next book, they upload the draft on the finished audit's page:
a smaller quote on the draft alone, the same PayPal checkout, and only the places where the
draft disagrees with what is already published. The same service is an MCP server, so an
author's AI assistant can price, order, check and read the findings — while the payment
approval always goes to the person.

What the author takes away: a report that reads like an editor's note ("John Watson's wound:
shoulder in A Study in Scarlet vs leg in The Sign of the Four", both passages, the fix), the
series bible as a browsable page and as a spreadsheet with the passage behind every row, and the
findings as JSON. If a paid audit cannot finish, the author chooses a free retry or a full
refund through PayPal.

## How we built it
Python. Extraction with a cheap long-context model under a strict schema with mandatory
verbatim quotes (claims whose quote isn't in the source are dropped). Local heuristics find
candidate conflicts for free. Batched adjudication sends the series text once per batch as a
stable prefix. PayPal Orders v2 in the sandbox: OAuth → create (PAY_NOW) → buyer approves →
capture → job runs in a background thread, persisted as JSON. A seeded-defect benchmark
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
  Bohemia*. Fixes aimed at the later book. Free tier; about $1.60 at list price.
- Cleared 18 candidates, each by quoting the passage that clears it — e.g. Holmes "dying" at
  the Reichenbach Falls and then acting in *The Hound* ("aided by the diary which I kept at the
  time": it is set earlier). Two of its four contradictions were wrong (a disguise; a maiden
  name), and the README says so rather than hiding it.
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
  at extraction, not judgement; two prompt changes measured by the same harness took recall on
  planted chunks from 7/20 to 15/20. The README table is generated, never typed.
- A free tier is a budget, not a limit. Daily caps (and the discovery that 503s count against
  them) turned into a client-side budget guard, a model fallback ladder, a cache that makes
  every run resumable, and `build --dry-run` that says exactly what a run will spend.
- Test where the users are. A line-ending change passed every Linux test and broke a draft
  check on Windows, the maintainer's own platform; CI now runs on both.
- The real PayPal sandbox differs from any fake: orders start `PAYER_ACTION_REQUIRED`, not
  `CREATED`; the create response omits the amount. Running `doctor --paypal` against the
  sandbox found two bugs the in-process fake had hidden.

## What's next
Vocative names in dialogue as `first_name` claims (Mary Watson calling her husband "James" is
still missed); subscriptions for ongoing series via PayPal Subscriptions; a hosted instance.

## Built with
python, fastapi, paypal-orders-v2, mcp, gemini-api, ollama, pydantic, jinja2, rapidfuzz, tiktoken, render

## PayPal tools used
PayPal REST Orders v2 (sandbox): create order with `payment_source.paypal.experience_context`
(`PAY_NOW`, return/cancel URLs), capture on return, idempotency via `PayPal-Request-Id`;
Payments v2 capture refunds (`/v2/payments/captures/{id}/refund`) when a paid audit fails
and the buyer chooses their money back over a free retry (web button or MCP tool).
Verified against the sandbox (`canonkeeper doctor --paypal`): orders come back
`PAYER_ACTION_REQUIRED` with a `payer-action` link; early captures answer `ORDER_NOT_APPROVED`
and are handled. Two surfaces: the web app (`canonkeeper serve`) and an MCP server
(`canonkeeper mcp`) through which an author's AI assistant quotes and orders the audit while
the person approves the payment on PayPal — agent-initiated, human-approved commerce.
`scripts/mcp_flow_demo.py` shows the whole exchange from the client side.

Nothing runs on money that has not arrived: a job becomes paid in exactly one function
(`payments.settle`), reached from PayPal's return redirect, from the MCP `confirm_payment` tool,
or from a "check payment" button for a buyer who approved and closed the tab. It requires a
completed capture (not a PENDING one) of exactly the quoted amount and currency, and the agent
never holds a credential that could approve. Model calls start only after that check passes.

## AI tools used
Gemini API through its OpenAI-compatible endpoint: `gemini-3.5-flash-lite` for extraction
(two passes per chunk, medium reasoning effort) and `gemini-3.6-flash` for adjudication, both
on the free tier with a fallback ladder for overloaded models. Provider-agnostic: measured
locally on a laptop CPU with Ollama and `qwen3:4b` for private manuscripts (a sourced bible and
candidate list; its verdicts are not trusted, and the evidence rule sends them to the author).

## Video beats (< 3:00)
See docs/DEMO_SCRIPT.md: problem → the Holmes finding → buy an audit (sandbox checkout) →
the same through an assistant over MCP (agent cannot approve) → `check` a draft → benchmark →
close.

## Testing instructions (paste into Devpost; fill the two blanks)
**Hosted (fastest):** open `<RENDER URL>`. Press *Try it with the demo series* (an invented
three-book series with five planted errors), then *Pay with PayPal*. PayPal sandbox buyer:
`<sandbox buyer email>` / `<password>` — no real money moves. You return to the job page, which
updates itself; the audit takes about a minute (instant if someone ran it before you: demo-series
responses are shared). Open the report. *See what you get first* on the home page opens the real
six-volume Sherlock Holmes audit without paying.

The hosted instance runs on Gemini's free tier, which may use submitted text for training:
please upload only public-domain text or the demo series.

**Local, no keys at all (2 minutes):**
```bash
git clone https://github.com/nolankido/canonKeeper && cd canonKeeper
pip install -e ".[dev]"
canonkeeper demo --dest build/demo --open-report   # whole pipeline offline, rule-based model
canonkeeper serve --provider mock       # Preflight with simulated PayPal: http://127.0.0.1:8787
# in a second terminal, while serve runs: the same flow driven by an assistant over MCP
python scripts/mcp_flow_demo.py --brief --auto-approve --provider mock build/demo/books/*.txt
```
With `PAYPAL_CLIENT_ID` /
`PAYPAL_CLIENT_SECRET` (sandbox) in `.env`, `serve` uses the real sandbox, and
`canonkeeper doctor --round-trip` proves create → approve → capture → refund against it.
