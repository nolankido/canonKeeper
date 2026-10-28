# A real agent buying from Preflight (Oct 28, 2026)

[`transcript.json`](../../src/canonkeeper/preflight/samples/agent-session.json) is a real session, not a script: Claude (Claude Code run headless, `claude -p`)
connected to Preflight's MCP endpoint and allowed only Preflight's tools, asked five things in
plain words. Every tool call and every reply in it is the agent's own; only the spending key is
masked. It was recorded and filmed by
[`scripts/video/agent_session.py`](../../scripts/video/agent_session.py), and the clip on
`/assistant` and in the README is that film, sped up.

| the person said | the agent did |
|---|---|
| "Open a $20 Preflight budget for checking my drafts, at most $6 a purchase." | `open_budget(20, max_per_purchase_usd=6)`, and handed back the PayPal link and the budget page |
| *(approves the $20 on PayPal: the one step an agent can never take)* | |
| "Audit the Saltmarsh demo series and pay from the budget." | `confirm_budget`, `quote_demo_series` ($5.05, under the cap), `pay_with_budget`, `audit_status`, `get_findings`: five contradictions, read back with their fixes (it also noticed the judge's explanations cite chapter numbers that do not match the quotes) |
| "The Oswin Reed one is wrong: he only faked his death. Mark it." | `mark_finding_wrong`, which returns a link: "You need to confirm it yourself" |
| *(confirms it on the job page: $0.50 back to the budget)* | |
| "Check the demo series' next draft against it, from the budget." | `get_demo_draft`, `check_draft` ($2.01), `pay_with_budget`, `get_findings`: two contradictions in the draft |
| "Close the budget." | `close_budget`: the unused $13.44 refunded through PayPal |

The money is PayPal's sandbox (test money), all of it real sandbox transactions: order
`53J38858875010816`, capture `4X960848M90389929` ($20.00), refund `4MC70466LM698940R` ($13.44).
The server was a local copy of Preflight (`canonkeeper serve --provider mock`), which answers the
demo series with real Gemini answers recorded for it (gemini-3.5-flash-lite reading,
gemini-3.6-flash judging, 2026-10-28) and replays them offline; the agent, the MCP calls and
the payments are live. Its first `close_budget` call left out the spending key and was
refused; it retried with the key. The person's PayPal step used PayPal's card button as a guest with
PayPal's published sandbox test card.
