# Sprint to submission (Devpost deadline Thu Nov 12, 12:00 PT · video by Nov 9)

Each turn takes the top open item that can be done now. **Q** = needs the day's Gemini quota
(resets 21:00 HST); **N** = needs Nolan; everything else is quota-free engineering.

## Critical path
| # | item | needs | status |
|---|---|---|---|
| 1 | Sandbox money path proven end to end (`canonkeeper doctor --round-trip`) | N | open |
| 2 | Real sandbox checkout recorded (docs/DEMO_SCRIPT.md, last section) | N | open |
| 3 | Splice the recording into the draft video (`assemble.py --replace buy=…`) | — | tooling done 10-27; waiting on 2 |
| 4 | Render deploy (Blueprint, keys pasted by Nolan) | N | open |
| 5 | Smoke-test the hosted URL (`scripts/smoke_hosted.py <url>`) | — | tooling done 10-27; waiting on 4 |
| 6 | Video uploaded to YouTube (< 3:00), Devpost form filled from docs/DEVPOST_SUBMISSION.md | N | open |

## Measurement (nightly, one model per stage)
| # | item | needs | status |
|---|---|---|---|
| 7 | A/B of the disguise/maiden-name judging prompt (e69808e): adopt or revert | Q | scheduled 10-27 21:14 |
| 8 | Benchmark run 3 (stricter plants, fixed scorer); archive + README row | Q | scheduled 10-27 21:14 |
| 9 | Full-corpus check of sweep-only salience hints, then eval; make default if it holds | Q | after 7–8 |

## Engineering backlog (quota-free; pick from the top)
- Recall at extraction is the weakest stage (8 of 12 benchmark misses): ideas need item 9's numbers first.
- Report: let an author mark "needs your judgement" items resolved and re-render.
- Preflight: email-free receipt page (order id, amount, capture id, refund status) for the buyer.

## Done (10-26 → 10-27)
Security (path traversal, open redirect), payment path (settle, capture checks, check payment,
refunds, held payments, races — three review passes), readable report/bible, CSV and downloads,
authors' file formats, parallel extraction, quota counter and Pacific-time fixes, ladder fixes,
`quota` / `compare` / `archive-eval` / `doctor --round-trip`, Windows CI.
