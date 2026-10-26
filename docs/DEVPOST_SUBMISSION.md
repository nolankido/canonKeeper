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
- Finds Watson's wandering war wound (shoulder in *A Study in Scarlet*, leg in *The Sign of the
  Four*) unassisted, with both quotes.  ← fill in once the real run is done
- Classifies an in-text explanation as `deliberate` rather than flagging it.
- Benchmark: ___ precision / ___ recall on N injected defects.  ← from `canonkeeper eval`

## What we learned
(fill in)

## What's next
Preflight as an MCP server so an author's assistant can order an audit; `check` a draft
before it goes to the editor; subscriptions for ongoing series via PayPal Subscriptions.

## Built with
python, fastapi, paypal-orders-v2, gemini-api, pydantic, jinja2, rapidfuzz, tiktoken

## PayPal tools used
PayPal REST Orders v2 (sandbox): create order with `payment_source.paypal.experience_context`
(`PAY_NOW`, return/cancel URLs), capture on return, idempotency via `PayPal-Request-Id`.

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
