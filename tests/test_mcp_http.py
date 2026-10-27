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
