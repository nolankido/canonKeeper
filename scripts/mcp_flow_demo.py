"""Drive Preflight the way an AI assistant would: over MCP, as a client.

    canonkeeper serve                      # in another terminal (fake PayPal, or the sandbox)
    python scripts/mcp_flow_demo.py demo/books/*.txt
    python scripts/mcp_flow_demo.py --auto-approve demo/books/*.txt   # fake mode only

The script spawns `canonkeeper mcp` over stdio, calls quote_series → order_audit, then hands
the approval link to the person (or, in fake mode with --auto-approve, submits the fake
approval page itself), then confirm_payment → audit_status → get_findings. It never holds a
PayPal credential and cannot approve the payment: that step is the person's, by design.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client


def _val(res):  # noqa: ANN001, ANN202
    if res.is_error:
        raise SystemExit(f"tool error: {res.content[0].text}")
    return res.structured_content or json.loads(res.content[0].text)


BRIEF = False


def _brief(label: str, data: dict) -> str:
    """One line per tool result: the part a person watching needs."""
    if label.startswith("quote_series"):
        return f"{data['price']['amount']} {data['price']['currency']} — {data['price']['explanation']}"
    if label.startswith("order_audit"):
        return f"PayPal order {data.get('order_id')} — approval link for the person: {data.get('approve_url')}"
    if label.startswith("confirm_payment"):
        paid = data.get("paid")
        return f"paid: {str(paid).lower()}" + (f" — {data['detail']}" if data.get("detail") else "")
    if label.startswith("audit_status"):
        return f"status: {data.get('status')} — {data.get('claims')} facts, {data.get('contradictions')} contradictions — {data.get('report_url')}"
    if label.startswith("get_findings"):
        return f"{data.get('facts')} facts, {data.get('candidates')} candidates judged"
    return json.dumps(data, ensure_ascii=False)[:160]


def _say(label: str, data: dict) -> None:
    if BRIEF:
        print(f"▶ {label:<44} {_brief(label, data)}")
        return
    print(f"\n▶ {label}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:1200])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("books", nargs="+", help="book files in reading order")
    ap.add_argument("--series", default="The Saltmarsh Chronicles")
    ap.add_argument("--provider", default=None, help="model provider for the mcp server")
    ap.add_argument("--workdir", default=None)
    ap.add_argument(
        "--auto-approve",
        action="store_true",
        help="fake PayPal only: submit the fake approval page instead of asking the person",
    )
    ap.add_argument("--brief", action="store_true", help="one line per tool call")
    a = ap.parse_args()
    global BRIEF
    BRIEF = a.brief

    args = ["mcp"]
    if a.provider:
        args += ["--provider", a.provider]
    if a.workdir:
        args += ["--workdir", a.workdir]
    params = StdioServerParameters(command="canonkeeper", args=args, env=dict(os.environ))
    books = [{"name": Path(p).name, "text": Path(p).read_text(encoding="utf-8")} for p in a.books]

    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))

        q = _val(await s.call_tool("quote_series", {"series_name": a.series, "books": books}))
        _say("quote_series", q)
        job = q["job_id"]

        o = _val(await s.call_tool("order_audit", {"job_id": job}))
        _say("order_audit", o)

        c = _val(await s.call_tool("confirm_payment", {"job_id": job}))
        _say("confirm_payment (before the person approves)", c)
        assert not c.get("paid"), "the agent must not be able to pay by itself"

        url = o["approve_url"]
        if a.auto_approve and "/fake-approve" in url:
            qs = dict(httpx.URL(url).params)
            resp = httpx.post(
                url.split("?")[0],
                data={"token": qs["token"], "return_to": qs["return_to"]},
                follow_redirects=True,
                timeout=30,
            )
            print(f"\n  (the person approves on the PayPal page — simulated: {resp.url})\n")
        else:
            print(f"\nOpen this link, approve the payment, then press Enter:\n  {url}")
            sys.stdin.readline()

        c = _val(await s.call_tool("confirm_payment", {"job_id": job}))
        _say("confirm_payment (after approval)", c)

        t0 = time.monotonic()
        while True:
            st = _val(await s.call_tool("audit_status", {"job_id": job}))
            if st["status"] in ("done", "failed") or time.monotonic() - t0 > 3600:
                break
            await asyncio.sleep(3)
        _say("audit_status", st)

        f = _val(await s.call_tool("get_findings", {"job_id": job}))
        _say("get_findings", {k: v for k, v in f.items() if k != "findings"})
        for row in f.get("findings", []):
            vals = " vs ".join(f"'{v['value']}' ({v['book']})" for v in row["values"][:3])
            print(f"  - [{row['severity']}] {row['subject']}.{row['predicate']}: {vals}")
            if row.get("suggested_fix"):
                print(f"      fix (later book): {row['suggested_fix'][:160]}")


if __name__ == "__main__":
    asyncio.run(main())
