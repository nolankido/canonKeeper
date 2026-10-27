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
deliberate reveal, or noise. The audit's price is known before it runs — the token count is —
so Preflight quotes it, takes payment through PayPal, and only then spends any compute.

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
- Benchmark (seeded defects, 6 volumes): ___ precision / ___ recall on 20 injected
  contradictions. ← from `canonkeeper eval`; the first run scored 1.00 / 0.05 and the
  harness pointed straight at extraction, which is the point of having one.

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
(`PAY_NOW`, return/cancel URLs), capture on return, idempotency via `PayPal-Request-Id`.
Verified against the sandbox (`canonkeeper doctor --paypal`): orders come back
`PAYER_ACTION_REQUIRED` with a `payer-action` link; early captures answer `ORDER_NOT_APPROVED`
and are handled. Two surfaces: the web app (`canonkeeper serve`) and an MCP server
(`canonkeeper mcp`) through which an author's AI assistant quotes and orders the audit while
the person approves the payment on PayPal — agent-initiated, human-approved commerce.
`scripts/mcp_flow_demo.py` shows the whole exchange from the client side.

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
