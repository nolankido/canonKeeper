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


def test_every_tool_says_what_it_does_to_the_world(tmp_path):
    """MCP annotations: an assistant can tell reading from spending before it acts."""
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    tools = {t.name: t for t in asyncio.run(build_server(app.state.mcp_service).list_tools())}
    assert all(t.annotations and t.annotations.title for t in tools.values())
    a = {n: t.annotations for n, t in tools.items()}
    assert a["get_findings"].read_only_hint and a["budget_status"].read_only_hint
    assert a["pay_with_budget"].destructive_hint and a["delete_job"].destructive_hint
    assert a["close_budget"].destructive_hint and a["close_budget"].open_world_hint
    assert not a["quote_series"].read_only_hint and not a["quote_series"].destructive_hint


def test_the_private_page_gives_the_local_steps_and_the_home_page_links_it(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    web = TestClient(create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf"))
    page = web.get("/private").text
    assert "canonkeeper serve --provider openweights" in page and "ollama pull qwen3:4b" in page
    assert "not verdicts to trust" in page  # the measured limit of a small model, said plainly
    assert 'href="/private"' in web.get("/").text


def test_the_assistant_page_shows_the_recorded_agent_session(tmp_path):
    """Judge review: judges need a way to see an assistant buy without an MCP client."""
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    web = TestClient(create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf"))
    page = web.get("/assistant").text
    assert 'data-testid="agent-session"' in page
    assert "<b>open_budget</b>" in page and "<b>close_budget</b>" in page
    assert "You approve the $20 on PayPal" in page
    import re

    assert 'budget_key="pfk_…"' in page and not re.search(r"pfk_[A-Za-z0-9]", page)  # masked
