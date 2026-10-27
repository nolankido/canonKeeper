"""The MCP server over its real transport: a client spawns `canonkeeper mcp` on stdio, as an
assistant would, and walks the whole paid flow. The agent can quote and order but not pay;
the person's approval (here, on the shared fake PayPal) is what lets confirm_payment through."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client

from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.paypal import FakePayPal


def _data(res: Any) -> dict[str, Any]:
    assert not res.is_error, res.content[0].text
    return res.structured_content or json.loads(res.content[0].text)


def test_an_assistant_drives_the_paid_flow_over_stdio(tmp_path: Path) -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PAYPAL_", "GEMINI_", "CANONKEEPER_"))
    }
    env.update({"PAYPAL_MODE": "fake", "PYTHONIOENCODING": "utf-8"})
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "canonkeeper.cli", "mcp", "--provider", "mock", "--workdir", str(tmp_path)],
        env=env,
        cwd=str(tmp_path),  # no stray .env
    )
    books = [
        {"name": "01-the-harbour.txt", "text": BOOK1},
        {"name": "02-the-wreck.txt", "text": BOOK2},
        {"name": "03-the-return.txt", "text": BOOK3},
    ]
    fake = FakePayPal(state_path=tmp_path / "preflight" / "fake-paypal.json")

    async def flow() -> None:
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"quote_series", "order_audit", "confirm_payment", "get_findings"} <= names
            q = _data(await s.call_tool("quote_series", {"series_name": "S", "books": books}))
            job = q["job_id"]
            o = _data(await s.call_tool("order_audit", {"job_id": job}))
            assert o["approve_url"].startswith("http")
            c = _data(await s.call_tool("confirm_payment", {"job_id": job}))
            assert c["paid"] is False  # the agent cannot pay by itself

            fake.approve(o["order_id"])  # the person approves on PayPal
            c = _data(await s.call_tool("confirm_payment", {"job_id": job}))
            assert c["paid"] is True
            deadline = time.monotonic() + 60
            while (st := _data(await s.call_tool("audit_status", {"job_id": job})))[
                "status"
            ] != "done":
                assert time.monotonic() < deadline, st
                await anyio.sleep(0.2)
            f = _data(await s.call_tool("get_findings", {"job_id": job}))
            assert len(f["findings"]) == 5 and all(row["summary"] for row in f["findings"])

    anyio.run(flow)
