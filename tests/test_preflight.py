"""Preflight end to end: upload → quote → order → (fake) approve → capture → audit → report.
Uses the fake PayPal and the mock provider; no network."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3, PLANTED
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import SANDBOX, FakePayPal, PayPalClient
from canonkeeper.preflight.pricing import quote


def test_quote_is_deterministic_and_explained():
    q = quote(0)
    assert q.total_usd == Decimal("5.00")  # minimum
    q = quote(900_000, words=600_000, books=6)
    assert q.usage_usd == Decimal("36.00")
    assert q.total_usd == Decimal("41.00")
    assert "per 100k tokens" in q.explanation
    assert q.total_str == "41.00"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        provider="mock",
        workdir=tmp_path / "wd",
        cache_dir=tmp_path / "wd" / "cache",
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        paypal=FakePayPal(),
        workdir=tmp_path / "pf",
        public_base_url="http://testserver",
    )
    return TestClient(app, follow_redirects=False)


def _upload(client):
    files = [
        ("files", ("03-the-return.txt", BOOK3.encode(), "text/plain")),
        ("files", ("01-the-harbour.txt", BOOK1.encode(), "text/plain")),
        ("files", ("02-the-wreck.txt", BOOK2.encode(), "text/plain")),
    ]
    r = client.post("/quote", data={"series_name": "The Saltmarsh Chronicles"}, files=files)
    assert r.status_code == 200, r.text
    return r


def test_full_flow_with_fake_paypal(client):
    assert client.get("/healthz").json()["paypal"] == "fake"

    r = _upload(client)
    assert "Pay $" in r.text and "with PayPal" in r.text
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store = client.app.state.store
    job = store.get(job_id)
    assert job.status == "quoted"
    assert job.books == ["01-the-harbour.txt", "02-the-wreck.txt", "03-the-return.txt"]
    assert job.tokens > 500

    # Nothing has run yet.
    assert not (store.dir(job_id) / "bible.jsonl").exists()

    # Order → redirected to the (fake) approval page.
    r = client.post(f"/jobs/{job_id}/order")
    assert r.status_code == 303
    approve = r.headers["location"]
    assert approve.startswith("/fake-approve?token=FAKE")
    job = store.get(job_id)
    assert job.status == "ordered" and job.order_id.startswith("FAKE")

    r = client.get(approve)
    assert r.status_code == 200 and "Pay Now" in r.text and job.quote_usd in r.text

    # Buyer approves → PayPal-style return → capture → job runs.
    r = client.post(
        "/fake-approve", data={"token": job.order_id, "return_to": "http://testserver/return"}
    )
    assert r.status_code == 303
    r = client.get(r.headers["location"])
    assert r.status_code == 303 and r.headers["location"] == f"/jobs/{job_id}"

    client.app.state.runner.wait(job_id, timeout=60)
    job = store.get(job_id)
    assert job.status == "done", job.error
    assert job.contradictions == len(PLANTED)
    assert client.app.state.paypal.get_order(job.order_id).status == "COMPLETED"

    page = client.get(f"/jobs/{job_id}")
    assert "Open the report" in page.text
    assert client.get(f"/jobs/{job_id}/report").status_code == 200
    assert "Ivy Marlowe" in client.get(f"/jobs/{job_id}/bible").text

    # A second return with the same token is idempotent.
    r = client.get(f"/return?token={job.order_id}")
    assert r.status_code == 303


def test_cancel_and_reorder(client):
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    r = client.get(f"/cancel?job={job_id}")
    assert r.status_code == 303
    assert client.app.state.store.get(job_id).status == "cancelled"
    r = client.post(f"/jobs/{job_id}/order")  # allowed again
    assert r.status_code == 303


def test_upload_requires_files(client):
    r = client.post("/quote", data={"series_name": "x"})
    assert r.status_code == 400


@respx.mock
def test_real_client_speaks_orders_v2():
    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    create = respx.post(f"{SANDBOX}/v2/checkout/orders").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "5O190127TN364715T",
                "status": "PAYER_ACTION_REQUIRED",
                "purchase_units": [{"amount": {"currency_code": "USD", "value": "41.00"}}],
                "links": [
                    {"rel": "self", "href": "x"},
                    {
                        "rel": "payer-action",
                        "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
                    },
                ],
            },
        )
    )
    capture = respx.post(f"{SANDBOX}/v2/checkout/orders/5O190127TN364715T/capture").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "5O190127TN364715T",
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [{"amount": {"currency_code": "USD", "value": "41.00"}}]
                        }
                    }
                ],
            },
        )
    )
    pp = PayPalClient("cid", "sec", mode="sandbox")
    o = pp.create_order(
        amount="41.00",
        currency="USD",
        description="d",
        return_url="r",
        cancel_url="c",
        reference="job1",
    )
    assert o.id == "5O190127TN364715T" and o.approve_url.startswith(
        "https://www.sandbox.paypal.com"
    )
    body = create.calls[0].request
    assert body.headers["Authorization"] == "Bearer tok"
    assert body.headers["PayPal-Request-Id"] == "job1"
    sent = json.loads(body.read())
    assert sent["intent"] == "CAPTURE"
    assert sent["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "41.00"}
    assert sent["payment_source"]["paypal"]["experience_context"]["user_action"] == "PAY_NOW"
    o2 = pp.capture_order(o.id)
    assert o2.status == "COMPLETED" and o2.amount == "41.00"
    assert capture.called


def test_mcp_service_flow_agent_quotes_human_approves_agent_collects(tmp_path):
    from canonkeeper.preflight.mcp_server import PreflightService, build_server

    settings = Settings(
        provider="mock",
        workdir=tmp_path / "wd",
        cache_dir=tmp_path / "wd" / "cache",
        _env_file=None,
    )
    pp = FakePayPal()
    svc = PreflightService(settings, pp, tmp_path / "pf", public_base_url="http://host")

    q = svc.quote_series(
        "The Saltmarsh Chronicles",
        [
            {"name": "01-the-harbour.txt", "text": BOOK1},
            {"name": "02-the-wreck.txt", "text": BOOK2},
            {"name": "03-the-return.txt", "text": BOOK3},
        ],
    )
    job_id = q["job_id"]
    assert q["price"]["amount"] == "5.05" and "per 100k tokens" in q["price"]["explanation"]

    o = svc.order_audit(job_id)
    assert o["status"] == "ordered" and o["approve_url"].startswith(
        "http://host/fake-approve?token="
    )
    # Agent must not pay: before approval, confirm reports not paid and nothing runs.
    c = svc.confirm_payment(job_id)
    assert c["paid"] is False and svc.audit_status(job_id)["status"] == "ordered"
    # The person approves on PayPal's page (simulated), then the agent confirms.
    pp.approve(o["order_id"])
    c = svc.confirm_payment(job_id)
    assert c["paid"] is True
    svc.runner.wait(job_id, timeout=60)
    st = svc.audit_status(job_id)
    assert st["status"] == "done" and st["report_url"] == f"http://host/jobs/{job_id}/report"
    f = svc.get_findings(job_id)
    assert f["facts"] == 40 and len(f["findings"]) == len(PLANTED)
    assert all(
        row["verdict"] == "contradiction" and row["values"][0]["quote"] for row in f["findings"]
    )
    assert len(svc.get_findings(job_id, include_cleared=True)["findings"]) >= len(PLANTED)
    assert svc.list_jobs()[0]["job_id"] == job_id

    server = build_server(svc)
    assert server.name == "preflight"


@respx.mock
def test_real_client_capture_before_approval_is_not_an_error():
    """The sandbox answers 422 ORDER_NOT_APPROVED to an early capture, and the minimal create
    response carries no amount. Neither should surface as an exception to the agent."""
    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(f"{SANDBOX}/v2/checkout/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "2YN2481966207993W",
                "status": "PAYER_ACTION_REQUIRED",
                "links": [
                    {
                        "rel": "payer-action",
                        "href": "https://www.sandbox.paypal.com/checkoutnow?token=2YN2481966207993W",
                    }
                ],
            },
        )
    )
    respx.post(f"{SANDBOX}/v2/checkout/orders/2YN2481966207993W/capture").mock(
        return_value=httpx.Response(
            422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]}
        )
    )
    respx.get(f"{SANDBOX}/v2/checkout/orders/2YN2481966207993W").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "2YN2481966207993W",
                "status": "PAYER_ACTION_REQUIRED",
                "purchase_units": [{"amount": {"currency_code": "USD", "value": "5.00"}}],
            },
        )
    )
    pp = PayPalClient("cid", "sec", mode="sandbox")
    o = pp.create_order(
        amount="5.00",
        currency="USD",
        description="d",
        return_url="r",
        cancel_url="c",
        reference="j",
    )
    assert o.amount == "5.00" and o.currency == "USD"  # filled from the request
    o2 = pp.capture_order(o.id)
    assert o2.status == "PAYER_ACTION_REQUIRED"


def test_fake_paypal_shares_orders_across_processes(tmp_path):
    """The agent (mcp) creates the order; the web app (serve) approves it; the agent sees it."""
    state = tmp_path / "pf" / "fake-paypal.json"
    agent = FakePayPal(state_path=state)
    o = agent.create_order(
        amount="5.00",
        currency="USD",
        description="d",
        return_url="r",
        cancel_url="c",
        reference="j",
    )
    web = FakePayPal(state_path=state)  # a second process
    assert web.get_order(o.id).status == "PAYER_ACTION_REQUIRED"
    web.approve(o.id)
    assert agent.capture_order(o.id).status == "COMPLETED"
    assert FakePayPal(state_path=state).get_order(o.id).status == "COMPLETED"


def test_upload_limits(tmp_path):
    from canonkeeper.preflight.jobs import MAX_BOOKS, JobStore

    store = JobStore(tmp_path / "pf")
    with pytest.raises(ValueError, match="at most"):
        store.create_from_upload("s", [(f"{i}.txt", b"x") for i in range(MAX_BOOKS + 1)])
    with pytest.raises(ValueError, match="not a text file"):
        store.create_from_upload("s", [("book.docx", b"PK\x03\x04\x00\x00binary")])
    with pytest.raises(ValueError, match="too large"):
        store.create_from_upload("s", [("big.txt", b"a" * (40 * 10**6 + 1))])
    job, _ = store.create_from_upload("   ", [("01 - The Harbour.txt", b"Once upon a time.")])
    assert job.series_name == "Untitled series" and job.books == ["01-The-Harbour.txt"]


def test_sample_report_and_no_public_job_list(tmp_path, client):
    r = client.get("/sample")
    assert r.status_code == 200 and "Jezail bullet" in r.text
    assert 'href="/sample"' in client.get("/").text
    _upload(client)
    assert ">The Saltmarsh Chronicles</a>" in client.get("/").text  # demo mode: one user

    class SandboxLike(FakePayPal):
        mode = "sandbox"

    settings = Settings(provider="mock", workdir=tmp_path / "w2", _env_file=None)
    public = TestClient(
        create_app(settings=settings, paypal=SandboxLike(), workdir=tmp_path / "pf")
    )
    assert ">The Saltmarsh Chronicles</a>" not in public.get("/").text  # same store, unlisted
