"""Judge review: an assistant could mark findings wrong and pull back up to half the price on
its own. Over MCP it now only hands the person a link; the money moves on their click."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.mcp_server import build_server
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


def test_the_assistant_gets_a_link_and_the_person_confirms(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(
        settings=settings,
        paypal=FakePayPal(),
        workdir=tmp_path / "pf",
        public_base_url="http://preflight.test",
    )
    web, svc = TestClient(app), app.state.mcp_service
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    first = svc.get_findings(job)["findings"][0]["index"]

    server = build_server(svc, hosted=True)
    result = asyncio.run(
        server.call_tool(
            "mark_finding_wrong", {"job_id": job, "finding_index": first, "note": "a disguise"}
        )  # fmt: skip
    )
    answer = result.structured_content or json.loads(result.content[0].text)
    answer = answer.get("result", answer)
    assert answer["marked"] is False and "You cannot do it for them" in answer["next"]
    assert answer["confirm_url"].startswith(f"http://preflight.test/jobs/{job}?mark={first}")
    assert svc.store.get(job).guarantee == []  # nothing moved
    order = svc.paypal.get_order(svc.store.get(job).order_id)
    assert not order.raw.get("partial_refunds")

    page = web.get(answer["confirm_url"].replace("http://preflight.test", "")).text
    assert f'id="mark-{first}"' in page and "your assistant asked you to confirm" in page
    assert 'value="a disguise"' in page
    web.post(f"/jobs/{job}/findings/{first}/wrong", data={"note": "a disguise"})  # the person
    assert svc.store.get(job).guarantee[0]["amount"] == "0.50"
    again = svc.ask_to_mark_wrong(job, first)
    assert again["reason"] == "already marked wrong"


def test_a_failed_audits_refund_is_confirmed_by_the_person_too(tmp_path):
    """Judge review: refund_failed_audit refunded on the assistant's say-so."""
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf",
                     public_base_url="http://preflight.test")  # fmt: skip
    svc = app.state.mcp_service
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    assert "only a failed audit" in svc.ask_to_refund(job)["reason"]
    svc.store.update(job, status="failed", order_id="O1", capture_id="C1")
    asked = svc.ask_to_refund(job)
    assert (
        asked["refunded"] is False
        and asked["confirm_url"] == f"http://preflight.test/jobs/{job}#refund"
    )
    assert svc.store.get(job).status == "failed" and svc.store.get(job).refund_id is None
    page = TestClient(app).get(f"/jobs/{job}").text
    assert 'id="refund"' in page
