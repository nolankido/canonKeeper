# Devpost submission draft — PayPal AI Hackathon 2026

Deadline: Thu Nov 12 2026, 12:00 PM PT (10:00 AM HST). Video < 3 min on YouTube, public repo
with LICENSE visible, functional demo (hosted URL or clear run instructions).

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
- On the six Holmes volumes (457,890 words) it extracted 2,331 sourced facts, screened 26
  candidate conflicts, and reported exactly one contradiction: Watson's war wound — "struck on
  the shoulder by a Jezail bullet" (*A Study in Scarlet*), "sat nursing my wounded leg" (*The
  Sign of the Four*), "the jezail bullet which I had brought back in one of my limbs" (*The
  Noble Bachelor*) — with the fix aimed at the later book. The error readers have argued about
  since 1890, found unassisted, on a free tier, for $0.21 at list price.
- Ruled Holmes's "dark" eyes in *The Sign of the Four* `deliberate` — "he is in disguise as an old
  master mariner; his true eye colour is grey" — and explained each of the 20 non-conflicts.
- Benchmark (seeded defects, 6 volumes): ___ precision / ___ recall on 20 injected
  contradictions. ← from `canonkeeper eval`; the first run scored 1.00 / 0.05 and the
  harness pointed straight at extraction, which is the point of having one.

## What we learned
(fill in)

## What's next
Heuristics for the two name slips the bible already holds as facts (Mrs. Hudson / Mrs. Turner
both recorded as the landlady; Mary Watson calling her husband James); subscriptions for
ongoing series via PayPal Subscriptions; a hosted instance.

## Built with
python, fastapi, paypal-orders-v2, gemini-api, pydantic, jinja2, rapidfuzz, tiktoken

## PayPal tools used
PayPal REST Orders v2 (sandbox): create order with `payment_source.paypal.experience_context`
(`PAY_NOW`, return/cancel URLs), capture on return, idempotency via `PayPal-Request-Id`.
Two surfaces: the web app (`canonkeeper serve`) and an MCP server (`canonkeeper mcp`) through
which an author's AI assistant quotes and orders the audit while the person approves the
payment on PayPal — agent-initiated, human-approved commerce.

## AI tools used
Gemini API (`gemini-3.8-flash`) through its OpenAI-compatible endpoint for extraction and
adjudication; provider-agnostic, also runs on Ollama / LM Studio for private manuscripts.

## Video beats (< 3:00)
0:00 the problem (one sentence, one one-star review)
0:20 upload the six Holmes volumes → quote with the arithmetic on screen
0:40 PayPal sandbox checkout → capture → job page ticking
1:05 the report: Watson's wound, John/James, Mrs Hudson/Turner — with quotes
1:35 `check` a draft: two slips flagged, one explained change marked deliberate
2:05 the benchmark table, one sentence on how it's measured
2:30 repo, MIT, "runs on a free tier, no card"
