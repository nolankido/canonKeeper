# Sprint to submission: 25 turns (written 2026-10-27)

Deadline Thu Nov 12, 12:00 PT (10:00 HST). Judging Dec 1–15; the hosted demo must work until then.
Full evaluation and reasoning: the shared doc "canonkeeper / Preflight — hackathon evaluation and
25-turn sprint". This file is the working copy; tick turns off here as they land.

**How to run a turn.** The quota turns (T2, T11, T13) run on their nights, after the Gemini day resets at
21:00 HST, measured on one model (`CANONKEEPER_MODEL_FALLBACKS_ENABLED=false`). Otherwise take the
lowest-numbered open turn whose inputs exist; if a Nolan step (N1–N6) is late, take the next turn that
does not need it. Never skip T21 (rehearsal) or T23 (buffer). Every turn ends with tests, ruff and mypy
green on Linux, `pytest` on the Windows laptop, a push, and a short status to Nolan.

**Judging (official rules).** Pass/fail gate: theme fit and "PayPal integration is central". Then five
equal criteria, ties broken in this order: Technological Implementation, Design, Potential Impact,
Innovation/Idea, Presentation (video < 3 min). At most one grand or honorable-mention prize plus one
sponsor prize. Target: Best Use of Agentic Commerce; sponsor: AG Grid (Render credits come with the deploy).

## Turns
| Turn | Date | Turn's job | Moves | Done when | Status |
| --- | --- | --- | --- | --- | --- |
| T1 | Oct 27 | Fix the review's correctness findings: price arithmetic shown wrong ("× 0.01 = $5.05"), `render.yaml` demo mode crashes at start, README `serve` line, MCP links ignore the port, a capture with no amount passes, raw slot text in draft-check verdicts, fake-mode home page lists every job, sample report's cost tile and its two known-wrong calls unlabelled | Tech, Design | Each has a regression test; sample page says which calls were wrong | done 10-27 |
| T2 | Oct 27 night (quota) | A/B the disguise/maiden-name judging prompt; benchmark run 3; archive; README row; regenerate the sample report if adopted | Tech | Adopted or reverted on numbers; row from result.md | open |
| T3 | Oct 28 | Lead with the product: README and home page open on Preflight (problem, who it is for, price, a finished Holmes report one click away); privacy stated plainly (hosted demo = public-domain only; private books via a no-training paid tier or local mode) | Design, Impact | A fresh agent says what it does, for whom, and how a private book is protected, in 10 seconds | done 10-27 |
| T4 | Oct 28 | Judging-proof hosting: real Gemini answers for the demo series shipped in the package, so demo runs cost 0 requests and a keyless local run shows real AI output, not mock text; quota checked before taking money; keep-awake through judging | Design, Tech | Cold start runs the demo with 0 requests; a quota-short quote refuses before PayPal | code done 10-27; the recording itself runs tonight after T2 (`scripts/record_demo.py`) |
| T5 | Oct 29 (after N1) | Hosted check: smoke test, fix deploy issues, real sandbox screenshots replace every fake-mode image, hosted URL into README and Devpost draft | Design, Presentation | `smoke_hosted.py <url> --order` green; no "simulated" screen left in README | live 10-27: https://preflight-kq1o.onrender.com, smoke test green with a real sandbox order; the paid job-page screenshot waits on one real sandbox checkout |
| T6 | Oct 29 | PayPal JS SDK buttons on the quote page (PayPal, Venmo, Pay Later); `PAYMENT.CAPTURE.COMPLETED` webhook as a fourth route into `settle`; receipt panel (order, capture, amount, refund) | Tech, Design | Sandbox buttons and webhook verified; tests green | code done 10-27; buttons and webhook verified on the sandbox at T5 |
| T7 | Oct 30 | Hosted MCP over streamable HTTP; books arrive by an upload link, not inline text; the assistant is a customer of the hosted shop and holds no merchant keys | Agentic commerce | Claude Desktop connects to the hosted URL and gets a quote | code done 10-27; Claude Desktop against the hosted URL at T5/N3 |
| T8 | Oct 31 | Agent budget (decision 2): the person funds a balance through PayPal once; the assistant spends it on audits and draft checks, never past it; every spend on a receipt; the rest refunds to PayPal | Agentic commerce, Innovation | Agent checks two drafts in sandbox and refunds the remainder | code done 10-27 (decision 2's default: prepaid balance); sandbox partial refund verified at T5 |
| T9 | Oct 31 | Finding guarantee (decision 1): an author marks a finding wrong and a capped partial refund goes back through PayPal | Innovation, Tech | Partial refund in sandbox; cap and double-refund tests | code done 10-27 (decision 1's default: $0.50 each, capped at half) |
| T10 | Nov 1 | Fresh-agent review of the new money paths (double spend, races, over-refund, webhook replay); fix | Tech | No open findings | done (76a28d4: 6 findings fixed, each a regression test) |
| T11 | Oct 30 night (quota) | Sweep-hints full-corpus bible + compare; eval; default if it holds | Tech | Numbers in the README either way | open |
| T12 | Nov 1 | Extraction recall: names spoken in dialogue, one-line descriptions in chapters about someone else (8 of the 12 misses) | Tech, Impact | Mock rules and tests cover both | done 10-27: one-line descriptions are the sentence hints (measured at T11); names in speech are `CANONKEEPER_EXTRACT_SPEECH_NAMES=true`, off until T13 measures it |
| T13 | Nov 1 night (quota) | Benchmark run 4 on T12 (`CANONKEEPER_EXTRACT_SPEECH_NAMES=true`, plus `EXTRACT_HINT_PASSES=sweep` if T11 adopted it; the sweep pass is re-requested, ~222 Flash-Lite); adopt or revert; README row | Tech | Recall up with precision held, or reverted | open |
| T14 | Nov 2 | Author workflow: resolve or reject "needs your judgement" items and re-render; report links back to its job and draft check | Design, Impact | Round trip works on the demo series | done |
| T15 | Nov 3 | AG Grid Community bible explorer: every fact with its passage, sort, filter, disputed rows flagged, CSV out (sponsor prize) | Design, sponsor | Smooth on the Holmes bible (3,963 facts) | done 10-27 (AG Grid Community 36.2.0, vendored; /sample/bible) |
| T16 | Nov 4 | One visual system across home, quote, job, report, bible; PayPal-branded pay button; phone width; print; fresh-eyes screenshot review | Design | Review finds nothing it would change | done 10-27: fresh-agent screenshot review, its findings fixed (af83773); the PayPal mark comes from the JS SDK buttons on the sandbox |
| T17 | Nov 4 | Video script v2: problem, audience and value in the first 15 seconds; real sandbox checkout; Claude Desktop buying a draft check from its budget; real numbers | Presentation | Script timed under 2:50 | done 10-27 (script v2: 363 words, 2:25 of speech) |
| T18 | Nov 7 (after N3) | Record every scene on the hosted instance; splice Nolan's two clips; captions | Presentation | Full cut assembled | open |
| T19 | Nov 8 | Narration (decision 4), final cut, fresh-agent review against the Presentation criterion | Presentation | Final file under 2:55 handed to Nolan | open |
| T20 | Nov 9 | Devpost text final: description, AI tools and how each is used (Gemini models; built with Claude Code), testing instructions, gallery images | Presentation | Every rule's requirement checked off | open |
| T21 | Nov 10 | Judge rehearsal: a fresh agent uses only the Devpost text and the hosted URL; fix what trips it | All | Rehearsal reaches the report | local rehearsal done 10-27 (its findings fixed: 708a11f and before); repeat on the hosted URL Nov 10 |
| T22 | Nov 10 | Hardening: model IDs, fallbacks, error pages, Windows tests, tag v1.0 | Tech | Linux and Windows green; tagged | open |
| T23 | Nov 11 | Buffer for whatever slipped | — | — | open |
| T24 | Nov 11 | Submit with Nolan (N6); license visible in the repo's About box; no code changes after this | All | Devpost shows Submitted | open |
| T25 | Dec 1–15 | Judging operations: deploys frozen, nightly benchmarks paused, daily smoke test as a scheduled task that alerts Nolan on failure | Design | Hosted demo green every day of judging | open |

## Nolan's steps
| # | Step | By | Unblocks |
|---|---|---|---|
| N1 | Render deploy: GitHub sign-in, New → Blueprint, paste GEMINI_API_KEY / PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, send the URL; then (optional, 3 min) developer.paypal.com → the sandbox app → Webhooks → add `<url>/paypal/webhook` for CHECKOUT.ORDER.APPROVED and PAYMENT.CAPTURE.COMPLETED and paste its ID as PAYPAL_WEBHOOK_ID | Oct 29 | T5 |
| N2 | `canonkeeper doctor --round-trip`, approve as the sandbox buyer | Oct 28 | proof of the real money path |
| N3 | Record the real sandbox checkout, and Claude Desktop buying an audit through the hosted MCP (Settings → Connectors → Add custom connector → `<render url>/mcp`) (after T6–T9) | Nov 7 | T18 |
| N4 | Voiceover, if decision 4 is "own voice" | Nov 8 | T19 |
| N5 | Upload the final cut to YouTube, public | Nov 9 | T20, T24 |
| N6 | Submit on Devpost; sandbox buyer login only in the private testing notes | Nov 11 | the entry |

## Decisions (default applies if unanswered by the turn that needs it)
1. Finding guarantee, T9: partial refund per finding marked wrong. Default yes, $0.50 each, capped at 50%.
2. Agent budget, T8: person funds a balance through PayPal once; the assistant spends it. Loosens
   "the agent never triggers a payment" (every movement of money into the balance is still approved by
   the person). Default yes, prepaid balance, refundable remainder (Vault needs account approval).
3. A counts-only result from Nolan's own catalog via local mode (no text leaves his laptop). Default skip.
4. Video voice: own voice / captions only / open-source synthetic voice. Default captions only.

## Done before the sprint (10-26 → 10-27)
Security (path traversal, open redirect), payment path (settle, capture checks, check payment, refunds,
held payments, races — three review passes), readable report/bible, CSV and downloads, authors' file
formats, parallel extraction, quota counter and Pacific-time fixes, ladder fixes, `quota` / `compare` /
`archive-eval` / `doctor --round-trip`, Windows CI, `assemble.py --replace`, `scripts/smoke_hosted.py`.
