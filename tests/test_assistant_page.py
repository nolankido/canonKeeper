"""The page that tells a person (or a judge) how to connect an AI assistant over MCP."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.mcp_server import build_server
from canonkeeper.preflight.paypal import FakePayPal


def test_the_assistant_page_gives_the_address_and_every_tool(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(
        settings=settings,
        paypal=FakePayPal(),
        workdir=tmp_path / "pf",
        public_base_url="http://preflight.test",
    )
    web = TestClient(app)
    page = web.get("/assistant", headers={"accept": "text/html"}).text
    assert 'data-testid="mcp-url"' in page
    url = "http://preflight.test/mcp"
    assert f'value="{url}"' in page and f"claude mcp add --transport http preflight {url}" in page
    names = [
        t.name for t in asyncio.run(build_server(app.state.mcp_service, hosted=True).list_tools())
    ]
    assert names and all(f"<code>{n}</code>" in page for n in names)
    assert "list_jobs" not in names  # hosted: nobody lists other people's jobs
    assert f"The {len(names)} tools it gets" in page
    assert "approve a payment: only you can" in page


def test_every_page_links_to_it(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    web = TestClient(create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf"))
    home = web.get("/").text
    assert 'href="/assistant" data-testid="nav-assistant"' in home
    assert 'data-testid="assistant-note"' in home
