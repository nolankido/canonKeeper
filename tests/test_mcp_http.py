"""T7: the MCP server hosted by the web service over streamable HTTP, as an assistant such as
Claude Desktop reaches it: a URL, no keys. The books arrive through an upload link the person
uses, the person approves the payment, and the assistant collects the findings."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import FakePayPal


def _data(res: Any) -> dict[str, Any]:
    assert not res.is_error, res.content[0].text
    return res.structured_content or json.loads(res.content[0].text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def hosted(tmp_path: Path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    pp = FakePayPal()
    app = create_app(
        settings=Settings(provider="mock", workdir=tmp_path / "w", _env_file=None),
        paypal=pp,
        workdir=tmp_path / "pf",
        public_base_url=base,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield base, app, pp
    server.should_exit = True
    thread.join(timeout=10)


def test_an_assistant_buys_an_audit_over_http_with_an_upload_link(hosted) -> None:
    base, app, pp = hosted

    async def flow() -> dict[str, Any]:
        async with streamable_http_client(f"{base}/mcp") as (read, write, *_):  # noqa: SIM117 - the session needs the open transport
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                names = {t.name for t in (await mcp.list_tools()).tools}
                assert {"request_upload_link", "upload_status", "order_audit"} <= names
                assert {"open_budget", "pay_with_budget", "close_budget"} <= names
                assert "list_jobs" not in names  # a public server lists nobody's jobs

                link = _data(
                    await mcp.call_tool("request_upload_link", {"series_name": "Saltmarsh"})
                )
                token = link["token"]
                assert link["upload_url"] == f"{base}/upload/{token}"
                waiting = _data(await mcp.call_tool("upload_status", {"token": token}))
                assert waiting["status"] == "waiting for the upload"

                # The person opens the link and uploads the books (not the assistant).
                async with httpx.AsyncClient() as web:
                    page = await web.get(link["upload_url"])
                    assert page.status_code == 200 and "Upload your series" in page.text
                    files = [
                        ("files", ("01-the-harbour.txt", BOOK1.encode(), "text/plain")),
                        ("files", ("02-the-wreck.txt", BOOK2.encode(), "text/plain")),
                        ("files", ("03-the-return.txt", BOOK3.encode(), "text/plain")),
                    ]
                    up = await web.post(
                        link["upload_url"], data={"series_name": "Saltmarsh"}, files=files
                    )
                    assert up.status_code == 200 and 'data-testid="via-agent"' in up.text
                    again = await web.post(
                        link["upload_url"], data={"series_name": "x"}, files=files[:1]
                    )
                    assert again.status_code == 409  # one link, one job

                quoted = _data(await mcp.call_tool("upload_status", {"token": token}))
                assert quoted["status"] == "quoted" and quoted["price"]["currency"] == "USD"
                job_id = quoted["job_id"]

                order = _data(await mcp.call_tool("order_audit", {"job_id": job_id}))
                early = _data(await mcp.call_tool("confirm_payment", {"job_id": job_id}))
                assert early.get("status") != "paid"  # the assistant cannot pay by itself

                pp.approve(order["order_id"])  # the person approves on PayPal's page
                _data(await mcp.call_tool("confirm_payment", {"job_id": job_id}))
                for _ in range(200):
                    status = _data(await mcp.call_tool("audit_status", {"job_id": job_id}))
                    if status["status"] in ("done", "failed"):
                        break
                    await anyio.sleep(0.1)
                assert status["status"] == "done"
                return _data(await mcp.call_tool("get_findings", {"job_id": job_id}))

    findings = anyio.run(flow)
    assert findings["findings"] and all(
        f["verdict"] == "contradiction" for f in findings["findings"]
    )


def test_an_unknown_or_used_upload_link_is_refused(hosted) -> None:
    base, _app, _pp = hosted
    assert httpx.get(f"{base}/upload/not-a-real-token-at-all").status_code == 404
    assert httpx.get(f"{base}/upload/..%2F..%2Fetc").status_code == 404


def test_mcp_accepts_its_public_host_and_guards_localhost() -> None:
    from canonkeeper.preflight.app import _mcp_security

    public = _mcp_security("https://preflight-abc.onrender.com")
    assert public.allowed_hosts == ["preflight-abc.onrender.com"]
    assert public.allowed_origins == ["https://preflight-abc.onrender.com"]
    local = _mcp_security("http://127.0.0.1:8787")
    assert local.enable_dns_rebinding_protection and "127.0.0.1:*" in local.allowed_hosts


def test_the_devpost_assistant_prompt_works_as_written(hosted) -> None:
    """Judge rehearsal: "Open a $20 budget", then "Audit the Saltmarsh demo series and pay from
    the budget", then check the next draft, with nothing but the tools. Every failure an
    assistant can cause explains itself, and every hint points at the budget."""
    base, app, pp = hosted

    async def flow() -> None:
        async with streamable_http_client(f"{base}/mcp") as (read, write, *_):  # noqa: SIM117
            async with ClientSession(read, write) as mcp:
                init = await mcp.initialize()
                assert "pay_with_budget" in (init.instructions or "")

                async def call(tool: str, **args: Any) -> Any:
                    return await mcp.call_tool(tool, args)

                b = _data(await call("open_budget", amount_usd=20))  # a number is fine
                pp.approve(app.state.mcp_service.budgets.get(b["budget_id"]).order_id)
                assert _data(await call("confirm_budget", budget_id=b["budget_id"]))["status"] == (
                    "active"
                )
                demo = _data(await call("quote_demo_series"))
                assert "pay_with_budget" in demo["next"]
                job = demo["job_id"]
                early = _data(await call("get_findings", job_id=job))
                assert early["finished"] is False and early["findings"] is None
                paid = _data(await call("pay_with_budget", job_id=job, budget_key=b["budget_key"]))
                assert paid["paid"] is True
                for _ in range(200):
                    st = _data(await call("audit_status", job_id=job))
                    if st["status"] in ("done", "failed"):
                        break
                    await anyio.sleep(0.1)
                assert (
                    st["status"] == "done"
                    and "capturing" not in st
                    and "get_findings" in st["next"]
                )
                assert len(_data(await call("get_findings", job_id=job))["findings"]) == 5

                draft = _data(await call("get_demo_draft"))
                check = _data(await call("check_draft", job_id=job, **draft))
                assert "pay_with_budget" in check["next"]

                # failures say why
                bad = await call("quote_series", series_name="x", books=[{"title": "a"}])
                assert (
                    bad.is_error
                    and '"name"' in bad.content[0].text
                    and '"text"' in bad.content[0].text
                )
                unknown = await call("audit_status", job_id="no-such-job")
                assert unknown.is_error and "unknown job" in unknown.content[0].text
                unpaid = await call("confirm_payment", job_id=check["job_id"])
                assert unpaid.is_error and "pay_with_budget" in unpaid.content[0].text
                titled = _data(await call("quote_series", series_name="T",
                                          books=[{"title": "01-a.txt", "text": "Ann has green eyes."}]))  # fmt: skip
                assert titled["job_id"]

    anyio.run(flow)


def test_the_person_can_use_the_demo_series_on_an_assistants_upload_link(hosted) -> None:
    base, app, _pp = hosted
    svc = app.state.mcp_service
    link = svc.request_upload_link("Saltmarsh")
    page = httpx.get(link["upload_url"]).text
    assert 'data-testid="upload-demo"' in page and "budget" in page
    r = httpx.post(f"{link['upload_url']}/demo")
    assert r.status_code == 200 and "tell it to go ahead" in r.text
    st = svc.upload_status(link["token"])
    assert st["status"] == "quoted" and "pay_with_budget" in st["next"]
    assert httpx.post(f"{link['upload_url']}/demo").status_code == 409  # one link, one job
