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
| T2 | Oct 27 night (quota) | A/B the disguise/maiden-name judging prompt; benchmark run 3; archive; README row; regenerate the sample report if adopted | Tech | Adopted or reverted on numbers; row from result.md | done 10-27 night: reverted on numbers (Barclay flipped right; Holmes's disguised eyes did not; Dr. Becher went from ambiguous to a wrong contradiction); benchmark run 3 judging resumes after the 10-28 quota reset |
| T3 | Oct 28 | Lead with the product: README and home page open on Preflight (problem, who it is for, price, a finished Holmes report one click away); privacy stated plainly (hosted demo = public-domain only; private books via a no-training paid tier or local mode) | Design, Impact | A fresh agent says what it does, for whom, and how a private book is protected, in 10 seconds | done 10-27 |
| T4 | Oct 28 | Judging-proof hosting: real Gemini answers for the demo series shipped in the package, so demo runs cost 0 requests and a keyless local run shows real AI output, not mock text; quota checked before taking money; keep-awake through judging | Design, Tech | Cold start runs the demo with 0 requests; a quota-short quote refuses before PayPal | done 10-27 night: recorded with gemini-3.5-flash-lite / gemini-3.6-flash after the revert (d6b70a1); the demo audit gives 5 contradictions (4 of 5 planted; Ivy's hair is called a contradiction, not left to the author) |
| T5 | Oct 29 (after N1) | Hosted check: smoke test, fix deploy issues, real sandbox screenshots replace every fake-mode image, hosted URL into README and Devpost draft | Design, Presentation | `smoke_hosted.py <url> --order` green; no "simulated" screen left in README | live 10-27: https://preflight-kq1o.onrender.com, smoke test green with a real sandbox order; the paid job-page screenshot waits on one real sandbox checkout |
| T6 | Oct 29 | PayPal JS SDK buttons on the quote page (PayPal, Venmo, Pay Later); `PAYMENT.CAPTURE.COMPLETED` webhook as a fourth route into `settle`; receipt panel (order, capture, amount, refund) | Tech, Design | Sandbox buttons and webhook verified; tests green | code done 10-27; buttons and webhook verified on the sandbox at T5 |
| T7 | Oct 30 | Hosted MCP over streamable HTTP; books arrive by an upload link, not inline text; the assistant is a customer of the hosted shop and holds no merchant keys | Agentic commerce | Claude Desktop connects to the hosted URL and gets a quote | code done 10-27; Claude Desktop against the hosted URL at T5/N3 |
| T8 | Oct 31 | Agent budget (decision 2): the person funds a balance through PayPal once; the assistant spends it on audits and draft checks, never past it; every spend on a receipt; the rest refunds to PayPal | Agentic commerce, Innovation | Agent checks two drafts in sandbox and refunds the remainder | code done 10-27 (decision 2's default: prepaid balance); sandbox partial refund verified at T5 |
| T9 | Oct 31 | Finding guarantee (decision 1): an author marks a finding wrong and a capped partial refund goes back through PayPal | Innovation, Tech | Partial refund in sandbox; cap and double-refund tests | code done 10-27 (decision 1's default: $0.50 each, capped at half) |
| T10 | Nov 1 | Fresh-agent review of the new money paths (double spend, races, over-refund, webhook replay); fix | Tech | No open findings | done (76a28d4: 6 findings fixed, each a regression test) |
| T11 | Oct 30 night (quota) | Sweep-hints full-corpus bible + compare; eval; default if it holds | Tech | Numbers in the README either way | side bible built 10-28 (139 Flash-Lite requests, fallbacks off): on the real text 3,963 → 3,686 facts (descriptive 2,304 → 1,951), screen 28 → 29 candidates (+20 −19): Mrs. Barclay and Dr. Becher's false conflicts gone, Watson's wound and the landlady kept, one new alias merge error (Hosmer Angel / James Windibank under Roylott); planted-fact probe 10 → 14 of 20. Decide on benchmark run 4 (T13), which needs ~12 judge requests: Oct 30 |
| T12 | Nov 1 | Extraction recall: names spoken in dialogue, one-line descriptions in chapters about someone else (8 of the 12 misses) | Tech, Impact | Mock rules and tests cover both | done 10-27: one-line descriptions are the sentence hints (measured at T11); names in speech are `CANONKEEPER_EXTRACT_SPEECH_NAMES=true`, off until T13 measures it |
| T13 | Nov 1 night (quota) | Benchmark run 4 on T12 (`CANONKEEPER_EXTRACT_SPEECH_NAMES=true`, plus `EXTRACT_HINT_PASSES=sweep` if T11 adopted it; the sweep pass is re-requested, ~222 Flash-Lite); adopt or revert; README row | Tech | Recall up with precision held, or reverted | open |
| T14 | Nov 2 | Author workflow: resolve or reject "needs your judgement" items and re-render; report links back to its job and draft check | Design, Impact | Round trip works on the demo series | done |
| T15 | Nov 3 | AG Grid Community bible explorer: every fact with its passage, sort, filter, disputed rows flagged, CSV out (sponsor prize) | Design, sponsor | Smooth on the Holmes bible (3,963 facts) | done 10-27 (AG Grid Community 36.2.0, vendored; /sample/bible) |
| T16 | Nov 4 | One visual system across home, quote, job, report, bible; PayPal-branded pay button; phone width; print; fresh-eyes screenshot review | Design | Review finds nothing it would change | done 10-27: fresh-agent screenshot review, its findings fixed (af83773); the PayPal mark comes from the JS SDK buttons on the sandbox |
| T17 | Nov 4 | Video script v2: problem, audience and value in the first 15 seconds; real sandbox checkout; Claude Desktop buying a draft check from its budget; real numbers | Presentation | Script timed under 2:50 | done 10-27 (script v2: 363 words, 2:25 of speech) |
| T18 | Nov 7 (after N3) | Record every scene on the hosted instance; splice Nolan's two clips; captions | Presentation | Full cut assembled | final take filmed 10-27 night: real sandbox checkout, recorded Gemini answers, the real agent session, voice-over; 2:53 |
| T19 | Nov 8 | Narration (decision 4), final cut, fresh-agent review against the Presentation criterion | Presentation | Final file under 2:55 handed to Nolan | narration done 10-27 (`scripts/video/narrate.py`: Piper, LibriTTS-R voice, timed to the captions, SRT for YouTube); the cut's assistant beat is the real agent session; final take after T4 |
| T20 | Nov 9 | Devpost text final: description, AI tools and how each is used (Gemini models; built with Claude Code), testing instructions, gallery images | Presentation | Every rule's requirement checked off | open |
| T21 | Nov 10 | Judge rehearsal: a fresh agent uses only the Devpost text and the hosted URL; fix what trips it | All | Rehearsal reaches the report | local rehearsal done 10-27 (its findings fixed: 708a11f and before); repeat on the hosted URL Nov 10 |
| T22 | Nov 10 | Hardening: model IDs, fallbacks, error pages, Windows tests, tag v1.0 | Tech | Linux and Windows green; tagged | open |
| T23 | Nov 11 | Buffer for whatever slipped | — | — | open |
| T24 | Nov 11 | Submit with Nolan (N6); license visible in the repo's About box; no code changes after this | All | Devpost shows Submitted | open |
| T25 | Nov 12–Dec 15 | Judging operations: deploys frozen from submission (the free instance's disk holds jobs and budgets; a redeploy clears them), nightly benchmarks paused, daily smoke test (`.github/workflows/live-check.yml`; a failed run emails Nolan) | Design | Hosted demo green every day of judging | smoke test live from Oct 27; freeze from Nov 12 |

## Judge review, 10-27 (a fresh agent scoring the live entry: about 33/50)
Fixed the same day: the claims it found unbacked (entity count, cost table, PayPal client size,
status list, an unsourced recall figure); every finding links to its row in the AG Grid bible,
whose view lives in its address; a quote has its own address (reload-safe); MCP tool
annotations; read-only share links (the job id is the owner's key); budget limits per purchase
and end date; a real finding on the home page; one banner; Devpost gallery and prize list.
Still open: sandbox buyer login in Devpost's private notes (N6) and the GitHub About link
(Nolan); Holmes sample's two known-wrong calls and the duplicate Becher item (T2 tonight,
then a measured candidate-dedupe change); a recording of a real assistant session.

## Judge review 4, 10-27 evening (a fresh agent, practice video included: 33/50)
Tech 8, Design 6, Impact 6, Innovation 7, Presentation 6. Fixed the same evening: the bible
explorer links back to its report, book headers are short and wrap, the chart's axes use AG
Charts 14's object form (a console warning before; the phone label size never applied), a
finding's passage scrolls into view; the video says "six Holmes books" (three are story
collections) and quotes the benchmark as its range; narration fills the dead air and says
what the models do; the assistant clip starts on its first message and has a poster frame;
Devpost's "$30 for six" is scoped to the Holmes books and the money-path review links its
tests. Open: the final take must show recorded Gemini answers, not the stand-in banner (after
T4); an agent session against the hosted URL (`agent_session.py --person`, two clicks by
Nolan); the GitHub About link (Nolan).

## Nolan's steps
| # | Step | By | Unblocks |
|---|---|---|---|
| N1 | Render deploy: GitHub sign-in, New → Blueprint, paste GEMINI_API_KEY / PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, send the URL; then (optional, 3 min) developer.paypal.com → the sandbox app → Webhooks → add `<url>/paypal/webhook` for CHECKOUT.ORDER.APPROVED and PAYMENT.CAPTURE.COMPLETED and paste its ID as PAYPAL_WEBHOOK_ID | Oct 29 | T5 |
| N2 | `canonkeeper doctor --round-trip`, approve as the sandbox buyer | Oct 28 | proof of the real money path |
| N3 | (No longer needed: the sandbox checkout and budget approval are filmed by the recorder; a branded assistant UI stays out of the video for the trademark rule.) Was: record the real sandbox checkout, and Claude Desktop buying an audit through the hosted MCP (Settings → Connectors → Add custom connector → `<render url>/mcp`) (after T6–T9) | Nov 7 | T18 |
| N4 | (Not needed: decision 4 is a synthetic open-source voice, which Nolan approved 10-27.) Optional: record your own voice from `scripts/video/narration.json` instead | Nov 8 | T19 |
| N5 | Upload the final cut to YouTube, public | Nov 9 | T20, T24 |
| N6 | Submit on Devpost; sandbox buyer login only in the private testing notes | Nov 11 | the entry |

## Decisions (default applies if unanswered by the turn that needs it)
1. Finding guarantee, T9: partial refund per finding marked wrong. Default yes, $0.50 each, capped at 50%.
2. Agent budget, T8: person funds a balance through PayPal once; the assistant spends it. Loosens
   "the agent never triggers a payment" (every movement of money into the balance is still approved by
   the person). Default yes, prepaid balance, refundable remainder (Vault needs account approval).
3. A counts-only result from Nolan's own catalog via local mode (no text leaves his laptop). Default skip.
4. Video voice: own voice / captions only / open-source synthetic voice. Decided 10-27: open-source synthetic voice plus captions (Nolan: "great").

## Done before the sprint (10-26 → 10-27)
Security (path traversal, open redirect), payment path (settle, capture checks, check payment, refunds,
held payments, races — three review passes), readable report/bible, CSV and downloads, authors' file
formats, parallel extraction, quota counter and Pacific-time fixes, ladder fixes, `quota` / `compare` /
`archive-eval` / `doctor --round-trip`, Windows CI, `assemble.py --replace`, `scripts/smoke_hosted.py`.
