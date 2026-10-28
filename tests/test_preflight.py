"""Preflight end to end: upload → quote → order → (fake) approve → capture → audit → report.
Uses the fake PayPal and the mock provider; no network."""

from __future__ import annotations

import json
import time
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


@pytest.mark.parametrize(
    ("tokens", "kind", "shown"),
    [
        (1_250, "audit", "$5.00 base + 1,250 tokens at $4.00 per 100k tokens ($0.05) = $5.05"),
        (150, "check", "$2.00 base + 150 tokens at $4.00 per 100k tokens ($0.01) = $2.01"),
        (
            630_000,
            "audit",
            "$5.00 base + 630,000 tokens at $4.00 per 100k tokens ($25.20) = $30.20",
        ),
    ],
)
def test_quote_arithmetic_on_screen_adds_up(tokens, kind, shown):
    """Regression: the line once read "× 0.01 = $5.05" and "× 0.00 = $2.01" because the multiplier
    was rounded for display while the total used the exact count."""
    q = quote(tokens, kind=kind)
    assert q.explanation == shown
    assert shown.endswith(f"${q.total_str}")


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
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/"), r.text
    r = client.get(r.headers["location"])  # the quote lives at the job's own address
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
    r = client.post("/fake-approve", data={"token": job.order_id})
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

    # The customer deletes their files: everything goes but the receipt.
    assert "Delete my files" in page.text
    assert client.post(f"/jobs/{job_id}/delete").status_code == 400  # box not ticked
    r = client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"})
    assert r.status_code == 303
    assert [p.name for p in store.dir(job_id).iterdir()] == ["job.json"]
    job = store.get(job_id)
    assert job.status == "deleted" and job.books == [] and job.order_id
    assert "Ivy Marlowe" not in store._path(job_id).read_text()
    assert client.get(f"/jobs/{job_id}/report").status_code == 404
    assert "have been deleted" in client.get(f"/jobs/{job_id}").text


def test_cancel_and_reorder(client):
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    r = client.get(f"/cancel?job={job_id}")
    assert r.status_code == 303
    assert client.app.state.store.get(job_id).status == "cancelled"
    r = client.post(f"/jobs/{job_id}/order")  # allowed again
    assert r.status_code == 303
    # Not once the buyer approved: the return would capture and run on missing files.
    client.app.state.paypal.approve(client.app.state.store.get(job_id).order_id)
    assert client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"}).status_code == 409


def test_an_order_never_approved_does_not_stop_the_files_being_deleted(client):
    """Judge review: a buyer who closed PayPal's window could not delete their books, and the
    unpaid-quote pruning skipped them. The order is released first; approving it later pays
    nothing, because a deleted job is never captured."""
    store, pp = client.app.state.store, client.app.state.paypal
    job_id = _upload(client).text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    order_id = store.get(job_id).order_id
    assert store.get(job_id).status == "ordered"
    assert client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"}).status_code == 303
    assert store.get(job_id).status == "deleted" and not (store.dir(job_id) / "books").exists()
    pp.approve(order_id)  # the person approves the old tab anyway
    client.get(f"/return?token={order_id}")
    assert pp.get_order(order_id).status != "COMPLETED"  # nothing captured
    assert store.get(job_id).status == "deleted"


def test_a_week_old_unapproved_order_is_pruned_like_a_quote(client):
    from datetime import UTC, datetime, timedelta

    store = client.app.state.store
    job_id = _upload(client).text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    import json

    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    path = store.dir(job_id) / "job.json"
    data = json.loads(path.read_text(encoding="utf-8")) | {"updated_at": old}
    path.write_text(json.dumps(data), encoding="utf-8")  # (save() would stamp it fresh)
    assert store.prune_unpaid() == 1 and store.get(job_id) is None


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
    assert f["facts"] == 41 and len(f["findings"]) == len(PLANTED)
    assert all(
        row["verdict"] == "contradiction" and row["values"][0]["quote"] for row in f["findings"]
    )
    summaries = {row["summary"] for row in f["findings"]}
    assert "Ivy Marlowe’s eye colour: green in The Harbour vs blue in The Return" in summaries
    assert not any(".died" in x or "_" in x for x in summaries)  # readable, not slot labels
    assert len(svc.get_findings(job_id, include_cleared=True)["findings"]) >= len(PLANTED)
    assert svc.list_jobs()[0]["job_id"] == job_id

    # The author's next draft, through the same agent: quote, person approves, collect.
    from canonkeeper.demo import DRAFT4

    chk = svc.check_draft(job_id, "04-the-ledger.txt", DRAFT4)
    assert chk["checks_against"] == job_id and chk["price"]["amount"].startswith("2.")
    o2 = svc.order_audit(chk["job_id"])
    assert svc.confirm_payment(chk["job_id"])["paid"] is False
    pp.approve(o2["order_id"])
    assert svc.confirm_payment(chk["job_id"])["paid"] is True
    svc.runner.wait(chk["job_id"], timeout=60)
    found = svc.get_findings(chk["job_id"])["findings"]
    assert {(r["subject"], r["predicate"]) for r in found} == {
        ("Ivy Marlowe", "eye_color"),
        ("Tobias Crane", "birthplace"),
    }

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
    job, _ = store.create_from_upload("s", [("01.txt", b"One."), ("02.txt", b"Two.")])
    assert job.books == ["01-01.txt", "02-02.txt"]  # a bare number is the name, not numbering
    assert [b.title for b in store.load_series(job).books] == ["01", "02"]


def test_sample_report_and_no_public_job_list(tmp_path, client):
    r = client.get("/sample")
    assert r.status_code == 200 and "Jezail bullet" in r.text
    assert 'href="/sample"' in client.get("/").text
    _upload(client)
    assert ">The Saltmarsh Chronicles</a>" in client.get("/").text  # this browser's own job
    stranger = TestClient(client.app)  # same server, another browser (no cookie)
    assert ">The Saltmarsh Chronicles</a>" not in stranger.get("/").text
    stranger.cookies.set("preflight_jobs", "../etc.%2E%2E.nope")
    assert stranger.get("/").status_code == 200  # junk in the cookie is ignored

    class SandboxLike(FakePayPal):
        mode = "sandbox"

    settings = Settings(provider="mock", workdir=tmp_path / "w2", _env_file=None)
    public = TestClient(
        create_app(settings=settings, paypal=SandboxLike(), workdir=tmp_path / "pf")
    )
    assert ">The Saltmarsh Chronicles</a>" not in public.get("/").text  # same store, unlisted


def test_demo_series_button_quotes_saltmarsh(client):
    assert 'action="/quote-demo"' in client.get("/").text
    r = client.post("/quote-demo", follow_redirects=True)
    assert r.status_code == 200 and "The Saltmarsh Chronicles" in r.text and "Pay $" in r.text
    job = client.app.state.store.all()[0]
    assert job.books == ["01-the-harbour.txt", "02-the-wreck.txt", "03-the-return.txt"]


def test_free_tier_privacy_banner(tmp_path):
    settings = Settings(
        provider="gemini", gemini_api_key="x", workdir=tmp_path / "w", _env_file=None
    )
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    home, other = TestClient(app).get("/").text, TestClient(app).get("/assistant").text
    assert "do not upload an unpublished manuscript" in home  # its own privacy card, said once
    assert "never an unpublished manuscript" not in home
    assert "never an unpublished manuscript" in other  # every other page: the banner
    local = Settings(provider="openweights", workdir=tmp_path / "w2", _env_file=None)
    app2 = create_app(settings=local, paypal=FakePayPal(), workdir=tmp_path / "pf2")
    assert "never an unpublished manuscript" not in TestClient(app2).get("/assistant").text


def test_job_progress_is_recorded_while_running(client):
    """The runner records stage and progress so the job page and audit_status show it."""
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store, runner = client.app.state.store, client.app.state.runner
    job = store.update(job_id, status="running")  # progress is only written to a running job
    seen: list[tuple[str, int, int]] = []
    tick = runner._progress_writer(job)
    for args in [("extracting", 0, 3), ("extracting", 3, 3), ("judging", 0, 5), ("judging", 5, 5)]:
        tick(*args)
        j = store.get(job_id)
        seen.append((j.stage, j.progress_done, j.progress_total))
    assert seen[1] == ("extracting", 3, 3) and seen[-1] == ("judging", 5, 5)
    page = client.get(f"/jobs/{job_id}").text
    assert "Judging candidate conflicts" in page and "5 of 5 candidates" in page


def test_unpaid_quotes_are_pruned_after_a_day(tmp_path):
    import json as _json

    from canonkeeper.preflight.jobs import JobStore

    store = JobStore(tmp_path / "pf")
    old, _ = store.create_from_upload("Old", [("a.txt", b"Once.")])
    fresh, _ = store.create_from_upload("Fresh", [("a.txt", b"Twice.")])
    paid, _ = store.create_from_upload("Paid", [("a.txt", b"Thrice.")])
    paid.status = "done"
    store.save(paid)
    for j in (old, paid):  # age two of them by two days
        p = store._path(j.id)
        d = _json.loads(p.read_text())
        d["updated_at"] = "2020-01-01T00:00:00+00:00"
        p.write_text(_json.dumps(d))
    assert store.prune_unpaid() == 1
    assert store.get(old.id) is None and not store.dir(old.id).exists()
    assert store.get(fresh.id) is not None and store.get(paid.id) is not None


def test_check_next_draft_against_a_finished_audit(client):
    """Audit a series, then check the next book's draft against it: a smaller quote, the
    same PayPal flow, and findings that only touch the draft (slips flagged, the reveal the
    draft explains marked deliberate)."""
    from canonkeeper.demo import BOOK1, BOOK2, BOOK3, DRAFT4

    files = [
        ("files", ("01-the-harbour.txt", BOOK1.encode(), "text/plain")),
        ("files", ("02-the-wreck.txt", BOOK2.encode(), "text/plain")),
        ("files", ("03-the-return.txt", BOOK3.encode(), "text/plain")),
    ]
    r = client.post(
        "/quote",
        data={"series_name": "The Saltmarsh Chronicles"},
        files=files,
        follow_redirects=True,
    )
    audit_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal

    def pay(job_id: str) -> None:
        client.post(f"/jobs/{job_id}/order")
        order_id = store.get(job_id).order_id
        pp.approve(order_id)
        client.get(f"/return?token={order_id}")
        runner.wait(job_id, timeout=60)

    # Not before the audit is finished.
    early = client.post(
        f"/jobs/{audit_id}/check", files={"draft": ("04-the-ledger.txt", DRAFT4.encode())}
    )
    assert early.status_code == 409
    pay(audit_id)
    page = client.get(f"/jobs/{audit_id}").text
    assert "Writing the next book?" in page

    r = client.post(
        f"/jobs/{audit_id}/check",
        files={"draft": ("04-the-ledger.txt", DRAFT4.encode())},
        follow_redirects=True,
    )
    assert r.status_code == 200 and "Check <code>the-ledger.txt</code>" in r.text
    assert "Pay $2.01" in r.text or "$2.01" in r.text  # the quote, re-priced as a check
    check_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    check = store.get(check_id)
    assert check.kind == "check" and check.parent_id == audit_id
    assert check.quote_usd == "2.01"  # $2 base + the draft's tokens
    pay(check_id)
    check = store.get(check_id)
    assert pp.get_order(check.order_id).raw["description"].startswith("Preflight draft check")
    assert check.status == "done", check.error
    assert check.contradictions == 2  # Ivy's eyes, Tobias's birthplace
    report = (store.dir(check_id) / "reports" / "report.json").read_text()
    assert "Hester Vale" in report and '"deliberate"' in report
    # The check is self-contained: deleting the series audit does not break it.
    client.post(f"/jobs/{audit_id}/delete", data={"confirm": "yes"})
    assert client.get(f"/jobs/{check_id}/report").status_code == 200


def test_paid_jobs_interrupted_by_a_restart_are_resumed(tmp_path):
    import json as _json

    from canonkeeper.preflight.jobs import JobStore, Runner

    settings = Settings(
        provider="mock", workdir=tmp_path / "w", cache_dir=tmp_path / "c", _env_file=None
    )
    store = JobStore(tmp_path / "pf")
    files = [
        ("01-a.txt", BOOK1.encode()),
        ("02-b.txt", BOOK2.encode()),
        ("03-c.txt", BOOK3.encode()),
    ]
    stuck, _ = store.create_from_upload("Stuck", files)
    live, _ = store.create_from_upload("Live", files)
    unpaid, _ = store.create_from_upload("Unpaid", files)
    for job, status in ((stuck, "running"), (live, "running")):
        job.status = status
        store.save(job)
    p = store._path(stuck.id)  # last progress update long ago: the process died
    d = _json.loads(p.read_text())
    d["updated_at"] = "2020-01-01T00:00:00+00:00"
    p.write_text(_json.dumps(d))

    runner = Runner(store, settings)
    assert runner.resume_interrupted() == [stuck.id]  # not the fresh one, not the unpaid one
    runner.wait(stuck.id, timeout=60)
    assert store.get(stuck.id).status == "done"
    assert store.get(live.id).status == "running" and store.get(unpaid.id).status == "quoted"


def test_job_ids_from_the_url_never_leave_the_jobs_folder(tmp_path, client):
    """An encoded ".." as the job id once served <workdir>/reports/report.html."""
    secret = tmp_path / "pf" / "reports"
    secret.mkdir(parents=True, exist_ok=True)
    (secret / "report.html").write_text("SECRET")
    (secret / "bible.html").write_text("SECRET")
    for jid in ("%2E%2E", "%2e%2e", "..%2F..", "%2E"):
        for route in ("report", "bible"):
            r = client.get(f"/jobs/{jid}/{route}")
            assert r.status_code == 404 and "SECRET" not in r.text, (jid, route)
        assert client.get(f"/jobs/{jid}").status_code == 404
        assert client.post(f"/jobs/{jid}/delete", data={"confirm": "yes"}).status_code == 404
    assert (secret / "report.html").exists()


def test_a_draft_named_dot_dot_is_stored_inside_its_job(client):
    from canonkeeper.preflight.jobs import JobStore

    store: JobStore = client.app.state.store  # type: ignore[attr-defined]
    job, _ = store.create_from_upload("S", [("01.txt", BOOK1.encode())])
    job.status = "done"
    (store.dir(job.id) / "bible.jsonl").write_text("")
    store.save(job)
    check, _ = store.create_check(job.id, "..", b"A new chapter.")
    assert (store.dir(check.id) / "draft").is_dir()
    assert [p.name for p in (store.dir(check.id) / "draft").iterdir()] == ["draft.txt"]


def test_fake_checkout_only_returns_to_the_url_the_order_was_created_with(client):
    """The simulated approval page used to redirect to any `return_to` it was handed."""
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    r = client.post(f"/jobs/{job_id}/order")
    approve = r.headers["location"]
    assert "return_to" not in approve
    token = approve.split("token=")[1]
    r = client.post(
        "/fake-approve", data={"token": token, "return_to": "https://evil.example/phish"}
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"http://testserver/return?token={token}"
    assert client.get("/fake-approve", params={"token": "FAKENOPE"}).status_code == 404


def test_nothing_runs_on_a_pending_or_wrong_amount_capture(tmp_path):
    from canonkeeper.preflight.paypal import Order, capture_problem

    def done(**raw):
        return Order("O1", "COMPLETED", None, raw.pop("value", "41.00"), "USD", raw)

    assert capture_problem(done(), amount="41.00", currency="USD") is None
    assert capture_problem(done(value="41"), amount="41.00", currency="USD") is None
    assert "quote was 41.00" in capture_problem(done(value="5.00"), amount="41.00", currency="USD")
    held = done(
        purchase_units=[
            {
                "payments": {
                    "captures": [{"status": "PENDING", "status_details": {"reason": "ECHECK"}}]
                }
            }
        ]
    )
    assert "PENDING, ECHECK" in capture_problem(held, amount="41.00", currency="USD")
    approved = Order("O1", "APPROVED", None, "41.00", "USD")
    assert "not completed" in capture_problem(approved, amount="41.00", currency="USD")

    class Underpaid(FakePayPal):
        def capture_order(self, order_id: str) -> Order:
            o = super().capture_order(order_id)
            o.amount = "0.01"
            return o

    settings = Settings(provider="mock", workdir=tmp_path / "wd", _env_file=None)
    c = TestClient(
        create_app(settings=settings, paypal=Underpaid(), workdir=tmp_path / "pf"),
        follow_redirects=False,
    )
    r = _upload(c)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    token = c.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
    back = c.post("/fake-approve", data={"token": token}).headers["location"]
    r = c.get(back)
    assert r.status_code == 402 and "quote was" in r.text
    job = c.app.state.store.get(job_id)  # type: ignore[attr-defined]
    assert job.status == "ordered" and not job.paid_at


def test_unpaid_quotes_are_evicted_oldest_first_past_the_disk_cap(tmp_path, client):
    import time

    from canonkeeper.preflight.jobs import JobStore

    store = JobStore(tmp_path / "cap")
    old, _ = store.create_from_upload("old", [("a.txt", b"x" * 400)])
    time.sleep(0.01)
    paid, _ = store.create_from_upload("paid", [("b.txt", b"y" * 400)])
    paid.status = "paid"
    store.save(paid)
    time.sleep(0.01)
    new, _ = store.create_from_upload("new", [("c.txt", b"z" * 400)])
    used = store._size(old.id) + store._size(new.id)
    store.make_room(400, limit=used + 100)  # one quote must go: the oldest unpaid one
    assert store.get(old.id) is None
    assert store.get(new.id) is not None and store.get(paid.id) is not None
    with pytest.raises(ValueError, match="too large"):
        store.make_room(10**6, limit=10**5)

    # the web form is refused on declared size, before anything is read
    big = [("files", ("big.txt", b"a" * (40 * 10**6 + 1), "text/plain"))]
    r = client.post("/quote", data={"series_name": "S"}, files=big)
    assert r.status_code == 413 and "limit 40 MB" in r.text


def test_buyer_who_closed_the_tab_after_approving_can_still_get_the_audit(client):
    """Approved on PayPal, never came back to /return: "check payment" captures and runs."""
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store, pp = client.app.state.store, client.app.state.paypal
    client.post(f"/jobs/{job_id}/order")
    job = store.get(job_id)
    assert "check payment" in client.get(f"/jobs/{job_id}").text

    r = client.post(f"/jobs/{job_id}/confirm")  # not approved yet: nothing captured
    assert r.status_code == 200 and "not approved yet" in r.text
    assert store.get(job_id).status == "ordered"

    pp.approve(job.order_id)  # the buyer approves on PayPal and closes the tab
    r = client.post(f"/jobs/{job_id}/confirm")
    assert r.status_code == 303
    client.app.state.runner.wait(job_id, timeout=60)
    assert store.get(job_id).status == "done"
    # coming back to /return late changes nothing and does not run it twice
    r = client.get(f"/return?token={job.order_id}")
    assert r.status_code == 303 and store.get(job_id).status == "done"


def test_a_failed_paid_audit_can_be_refunded_and_nothing_else_can(client):
    from canonkeeper.preflight.payments import refund

    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store, pp = client.app.state.store, client.app.state.paypal
    token = client.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
    client.get(client.post("/fake-approve", data={"token": token}).headers["location"])
    client.app.state.runner.wait(job_id, timeout=60)
    job = store.get(job_id)
    assert job.status == "done" and job.capture_id and job.capture_id.startswith("CAP")
    assert "only a failed audit" in refund(job, pp, store)  # delivered: no refund
    r = client.post(f"/jobs/{job_id}/refund", data={"confirm": "yes"})
    assert r.status_code == 409

    job.status, job.error = "failed", "quota exhausted"
    store.save(job)
    page = client.get(f"/jobs/{job_id}").text
    assert "Refund my payment" in page and "Retry" in page
    assert "resets at midnight Pacific" in page  # a quota failure is explained in plain words
    assert client.post(f"/jobs/{job_id}/refund").status_code == 400  # box not ticked
    r = client.post(f"/jobs/{job_id}/refund", data={"confirm": "yes"})
    assert r.status_code == 303
    job = store.get(job_id)
    assert job.status == "refunded" and job.refund_id.startswith("REF") and job.refunded_at
    assert "went back to your PayPal account" in client.get(f"/jobs/{job_id}").text
    # idempotent, and a refunded job cannot be retried into a free audit
    assert refund(job, pp, store) is None
    client.post(f"/jobs/{job_id}/retry")
    assert store.get(job_id).status == "refunded"
    assert client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"}).status_code == 303


@respx.mock
def test_real_client_refunds_the_whole_capture_once():
    from canonkeeper.preflight.paypal import PayPalClient

    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    route = respx.post(f"{SANDBOX}/v2/payments/captures/CAP1/refund").mock(
        side_effect=[
            httpx.Response(201, json={"id": "R1", "status": "COMPLETED"}),
            httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "CAPTURE_FULLY_REFUNDED"}],
                },
            ),
        ]
    )
    pp = PayPalClient("cid", "sec", mode="sandbox")
    r = pp.refund_capture("CAP1")
    assert (r.id, r.status) == ("R1", "COMPLETED")
    req = route.calls[0].request
    assert req.headers["PayPal-Request-Id"] == "refund-CAP1" and req.read() == b"{}"
    assert pp.refund_capture("CAP1").status == "COMPLETED"  # already refunded: done, not an error


def test_draft_titles_read_like_book_titles():
    from canonkeeper.preflight.jobs import draft_title

    assert draft_title("draft-04-the-ledger.txt") == "The Ledger (draft)"
    assert draft_title("04 - The Ledger.md") == "The Ledger (draft)"
    assert draft_title("draft.txt") == "New draft"


def test_runner_pings_its_own_url_while_a_job_runs(tmp_path, monkeypatch):
    """On a free host that sleeps when idle, a paid audit keeps the instance awake."""
    import threading

    import httpx as _httpx

    from canonkeeper.preflight.jobs import JobStore, Runner

    pings: list[str] = []
    monkeypatch.setattr(_httpx, "get", lambda url, **kw: pings.append(url))
    release = threading.Event()
    runner = Runner(JobStore(tmp_path / "pf"), Settings(provider="mock", _env_file=None))
    runner.keepalive_url, runner.keepalive_every = "http://host/healthz", 0.01
    monkeypatch.setattr(runner, "_run", lambda job_id: release.wait(5))
    runner.submit("job1")
    deadline = time.time() + 5
    while not pings and time.time() < deadline:
        time.sleep(0.01)
    assert pings and pings[0] == "http://host/healthz"
    release.set()
    runner.wait("job1", timeout=5)
    time.sleep(0.05)
    assert runner._keepalive is not None and not runner._keepalive.is_alive()  # stops when idle


def test_uploads_in_word_processor_encodings_are_read_not_refused(tmp_path):
    from canonkeeper.preflight.jobs import JobStore, decode_upload

    prose = "“Ivy Marlowe has green eyes,” said Tobias — naïvely.\n"
    assert decode_upload(prose.encode("utf-8")) == prose
    assert decode_upload(b"\xef\xbb\xbf" + prose.encode("utf-8")) == prose  # BOM dropped
    assert decode_upload(prose.encode("utf-16")) == prose  # Word's "Unicode text"
    assert decode_upload(prose.encode("utf-16-le")) == prose  # no BOM
    assert decode_upload(prose.encode("cp1252")) == prose  # old Windows tools
    crlf = prose.replace("\n", "\r\n") * 2
    assert decode_upload(crlf.encode("utf-8")) == prose * 2  # Windows line endings
    assert decode_upload(crlf.encode("utf-16")) == prose * 2
    with pytest.raises(ValueError, match="save it as plain text"):
        decode_upload(b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00", "book.docx")

    store = JobStore(tmp_path / "pf")
    job, _ = store.create_from_upload("S", [("01-the-harbour.txt", prose.encode("utf-16"))])
    assert store.load_series(job).books[0].text == prose


def test_finished_files_download_with_series_names(client):
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    token = client.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
    client.get(client.post("/fake-approve", data={"token": token}).headers["location"])
    client.app.state.runner.wait(job_id, timeout=60)
    assert "download/report.json" in client.get(f"/jobs/{job_id}").text
    r = client.get(f"/jobs/{job_id}/download/report.json")
    assert r.status_code == 200 and r.json()["claims_total"] > 0
    assert (
        'filename="the-saltmarsh-chronicles-audit-report.json"' in r.headers["content-disposition"]
    )
    assert client.get(f"/jobs/{job_id}/download/bible.html").status_code == 200
    csv_text = client.get(f"/jobs/{job_id}/download/bible.csv").content.decode("utf-8-sig")
    header, *rows = csv_text.splitlines()
    assert header.startswith("entity,fact,value,disputed,book,chapter")
    assert any(r.startswith("Ivy Marlowe,eye colour,green,yes,The Harbour") for r in rows)
    assert client.get(f"/jobs/{job_id}/download/job.json").status_code == 404  # not a file server
    assert client.get(f"/jobs/{job_id}/download/..%2Fjob.json").status_code == 404


# --- races and odd orders found in review ---------------------------------------------------


def _paid_job(client):
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    token = client.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
    client.get(client.post("/fake-approve", data={"token": token}).headers["location"])
    client.app.state.runner.wait(job_id, timeout=60)
    return job_id, token


def test_a_refund_in_flight_blocks_a_retry_and_a_run_stops_if_refunded(client):
    import threading

    from canonkeeper.preflight.jobs import _Stopped
    from canonkeeper.preflight.payments import refund

    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal
    job_id, _ = _paid_job(client)
    store.update(job_id, status="failed", error="quota")

    entered, release = threading.Event(), threading.Event()
    real = pp.refund_capture

    def slow_refund(cap):
        entered.set()
        release.wait(5)
        return real(cap)

    pp.refund_capture = slow_refund
    out: list = []
    t = threading.Thread(target=lambda: out.append(refund(store.get(job_id), pp, store)))
    t.start()
    assert entered.wait(5)
    assert store.get(job_id).status == "refunding"
    runner.submit(job_id)  # the buyer presses Retry while PayPal is refunding
    runner.wait(job_id, timeout=10)
    assert store.get(job_id).status == "refunding"  # the retry did not start
    release.set()
    t.join(5)
    job = store.get(job_id)
    assert out == [None] and job.status == "refunded" and job.refund_status == "COMPLETED"
    assert refund(job, pp, store) is None  # idempotent

    # and a run that is already going stops as soon as its job is no longer "running"
    other, _ = _paid_job(client)
    running = store.update(other, status="running")
    tick = runner._progress_writer(running)
    tick("extracting", 0, 3)
    store.update(other, status="deleted")
    with pytest.raises(_Stopped):
        tick("judging", 0, 3)
    assert store.get(other).status == "deleted"


def test_a_cancel_racing_a_capture_still_ends_paid(client):
    from canonkeeper.preflight.payments import settle

    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    job = store.get(job_id)
    pp.approve(job.order_id)
    real = pp.capture_order

    def capture_then_cancel(order_id):
        o = real(order_id)
        client.get(f"/cancel?job={job_id}")  # the back button lands mid-capture
        return o

    pp.capture_order = capture_then_cancel
    assert settle(job, pp, store, runner) is None
    runner.wait(job_id, timeout=60)
    job = store.get(job_id)
    assert job.status == "done" and job.capture_id


def test_reordering_after_an_approved_order_settles_it_instead_of_charging_twice(client):
    store, pp = client.app.state.store, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    first = store.get(job_id).order_id
    pp.approve(first)  # approved on PayPal ...
    client.get(f"/cancel?job={job_id}")  # ... then the buyer came back through cancel
    assert store.get(job_id).status == "cancelled"
    r = client.post(f"/jobs/{job_id}/order")  # "Try again"
    assert r.headers["location"] == f"/jobs/{job_id}"  # no second checkout
    client.app.state.runner.wait(job_id, timeout=60)
    job = store.get(job_id)
    assert job.order_id == first and job.status == "done"


def test_an_expired_order_lets_the_buyer_start_again(client):
    store, pp = client.app.state.store, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")

    def gone(order_id):
        req = httpx.Request("GET", f"https://api/v2/checkout/orders/{order_id}")
        raise httpx.HTTPStatusError(
            "404", request=req, response=httpx.Response(404, text="RESOURCE_NOT_FOUND", request=req)
        )

    pp.get_order = gone
    r = client.post(f"/jobs/{job_id}/confirm")
    assert r.status_code == 200 and "expired" in r.text
    assert store.get(job_id).status == "cancelled"  # "Try again" is offered, nothing stuck


def test_two_processes_sharing_the_fake_never_lose_each_others_orders(tmp_path):
    state = tmp_path / "fake-paypal.json"
    mcp, web = FakePayPal(state_path=state), FakePayPal(state_path=state)
    o = mcp.create_order(
        amount="5.00",
        currency="USD",
        description="d",
        return_url="r",
        cancel_url="c",
        reference="a",
    )
    web.approve(o.id)
    assert mcp.capture_order(o.id).status == "COMPLETED"
    web.create_order(
        amount="6.00",
        currency="USD",
        description="d",
        return_url="r",
        cancel_url="c",
        reference="b",
    )
    assert FakePayPal(state_path=state).get_order(o.id).status == "COMPLETED"


def test_a_held_capture_is_never_unpaid_and_starts_when_it_clears(client):
    from datetime import UTC, datetime, timedelta

    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    order_id = store.get(job_id).order_id
    pp.approve(order_id)
    real = pp.capture_order

    def held(oid):
        import copy

        o = copy.deepcopy(real(oid))  # what PayPal reports; the fake's own record is untouched
        o.raw["purchase_units"][0]["payments"]["captures"][0]["status"] = "PENDING"
        return o

    pp.capture_order = held
    r = client.post(f"/jobs/{job_id}/confirm")
    job = store.get(job_id)
    assert job.status == "held" and job.capture_id and "holding" in r.text
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    (store.dir(job_id) / "job.json").write_text(
        (store.dir(job_id) / "job.json").read_text().replace(job.updated_at, old)
    )
    store.prune_unpaid()
    store.make_room(10**8, limit=10**8)
    assert store.get(job_id).status == "held"  # money is in flight: never cleaned up
    assert client.post(f"/jobs/{job_id}/order").headers["location"] == f"/jobs/{job_id}"

    pp.capture_order = real  # the eCheck clears
    client.post(f"/jobs/{job_id}/confirm")
    runner.wait(job_id, timeout=60)
    assert store.get(job_id).status == "done"


def test_a_lock_left_by_a_dead_process_does_not_block(tmp_path):
    import os
    import time as _time

    from canonkeeper.preflight.jobs import JobStore

    store = JobStore(tmp_path / "pf")
    job, _ = store.create_from_upload("S", [("01.txt", b"One.")])
    lock = store.root / ".locks" / f"{job.id}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999" if os.name == "posix" else "0")
    if os.name != "posix":
        os.utime(lock, (_time.time() - 120, _time.time() - 120))
    t0 = _time.monotonic()
    assert store.update(job.id, status="cancelled") is not None
    assert _time.monotonic() - t0 < 2 and not lock.exists()


def test_the_watcher_resumes_a_job_whose_process_died(client):
    from datetime import UTC, datetime, timedelta

    store, runner = client.app.state.store, client.app.state.runner
    job_id, _ = _paid_job(client)
    store.update(job_id, status="running")  # its process "died" mid-run
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    path = store.dir(job_id) / "job.json"
    path.write_text(path.read_text().replace(store.get(job_id).updated_at, stale))
    stop = runner.watch(every_s=0.05)
    try:
        deadline = time.time() + 30
        while store.get(job_id).status != "done" and time.time() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
    assert store.get(job_id).status == "done"


def test_a_refund_with_no_answer_stays_refunding_and_can_be_checked_again(client):
    from datetime import UTC, datetime, timedelta

    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal
    job_id, _ = _paid_job(client)
    store.update(job_id, status="failed", error="quota")
    real = pp.refund_capture

    def no_answer(cap):
        real(cap)  # PayPal refunds ...
        raise httpx.ReadTimeout("no answer")  # ... but the reply is lost

    pp.refund_capture = no_answer
    r = client.post(f"/jobs/{job_id}/refund", data={"confirm": "yes"})
    assert r.status_code == 409 and "did not answer" in r.text
    assert store.get(job_id).status == "refunding"
    client.post(f"/jobs/{job_id}/retry")
    runner.wait(job_id, timeout=5)
    assert store.get(job_id).status == "refunding"  # no free audit on top of a refund
    assert "Check the refund with PayPal" in client.get(f"/jobs/{job_id}").text

    pp.refund_capture = real
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    path = store.dir(job_id) / "job.json"
    path.write_text(path.read_text().replace(store.get(job_id).updated_at, stale))
    client.post(f"/jobs/{job_id}/refund", data={"confirm": "yes"})
    assert store.get(job_id).status == "refunded"


def test_paypal_returning_with_a_replaced_order_still_pays_the_job(client):
    store, pp = client.app.state.store, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    first = store.get(job_id).order_id
    client.get(f"/cancel?job={job_id}")
    client.post(f"/jobs/{job_id}/order")  # a second order replaces the first ...
    assert store.get(job_id).earlier_orders == [first]
    pp.approve(first)  # ... while the buyer approves the first in another tab
    r = client.get(f"/return?token={first}")
    assert r.status_code == 303
    client.app.state.runner.wait(job_id, timeout=60)
    job = store.get(job_id)
    assert job.status == "done" and job.order_id == first


def test_no_model_key_means_no_quotes_and_no_payments(tmp_path):
    """Taking payment for a run that cannot happen is the one thing a paid service must not
    do: without a key the landing page says so and every paying route refuses."""
    settings = Settings(
        provider="gemini", gemini_api_key=None, workdir=tmp_path / "w", _env_file=None
    )
    c = TestClient(create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf"))
    assert "no model key configured" in c.get("/").text
    assert c.get("/healthz").json()["model"] == "no key"
    assert c.post("/quote-demo", follow_redirects=True).status_code == 503
    files = [("files", ("01.txt", b"Ivy has green eyes.", "text/plain"))]
    assert c.post("/quote", data={"series_name": "S"}, files=files).status_code == 503


def _held_job(client):
    import copy

    store, pp = client.app.state.store, client.app.state.paypal
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    client.post(f"/jobs/{job_id}/order")
    pp.approve(store.get(job_id).order_id)
    real = pp.capture_order

    def status(value):
        def capture(oid):
            o = copy.deepcopy(real(oid))
            o.raw["purchase_units"][0]["payments"]["captures"][0]["status"] = value
            return o

        pp.capture_order = capture

    status("PENDING")
    client.post(f"/jobs/{job_id}/confirm")
    assert store.get(job_id).status == "held"
    return job_id, status, real


def test_the_watcher_starts_a_held_job_once_paypal_clears_it(client):
    from canonkeeper.preflight.payments import settle

    store, runner, pp = client.app.state.store, client.app.state.runner, client.app.state.paypal
    job_id, _, real = _held_job(client)
    pp.capture_order = real  # cleared, and nobody presses anything
    stop = runner.watch(every_s=0.05, on_held=lambda j: settle(j, pp, store, runner))
    try:
        deadline = time.time() + 30
        while store.get(job_id).status != "done" and time.time() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
    assert store.get(job_id).status == "done"


def test_a_held_payment_that_is_declined_frees_the_job(client):
    store = client.app.state.store
    job_id, status, _ = _held_job(client)
    status("DECLINED")
    r = client.post(f"/jobs/{job_id}/confirm")
    assert "declined" in r.text and store.get(job_id).status == "cancelled"
    assert client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"}).status_code == 303


def test_a_refund_answered_with_a_server_error_stays_refunding(client):
    store, pp = client.app.state.store, client.app.state.paypal
    job_id, _ = _paid_job(client)
    store.update(job_id, status="failed", error="quota")

    def unavailable(cap):
        req = httpx.Request("POST", f"https://api/v2/payments/captures/{cap}/refund")
        raise httpx.HTTPStatusError("503", request=req, response=httpx.Response(503, request=req))

    pp.refund_capture = unavailable
    client.post(f"/jobs/{job_id}/refund", data={"confirm": "yes"})
    assert store.get(job_id).status == "refunding"  # unknown outcome: no Retry


def test_demo_series_runs_share_one_cache_and_uploads_do_not(client, monkeypatch):
    """Visitors trying the demo series all send the same invented text; sharing its responses
    means one run warms them and the rest cost no quota. Real uploads never share."""
    import canonkeeper.llm as llm_mod

    seen: list = []
    real = llm_mod.make_llm

    def recording(settings, **kw):
        seen.append(settings.cache_dir)
        return real(settings, **kw)

    monkeypatch.setattr(llm_mod, "make_llm", recording)
    store = client.app.state.store
    ids = []
    for _ in range(2):
        r = client.post("/quote-demo", follow_redirects=True)
        ids.append(r.text.split('action="/jobs/')[1].split("/order")[0])
    files = [("files", ("01-mine.txt", BOOK1.replace("Ivy", "Iris").encode(), "text/plain"))]
    r = client.post("/quote", data={"series_name": "Mine"}, files=files, follow_redirects=True)
    ids.append(r.text.split('action="/jobs/')[1].split("/order")[0])
    assert [store.get(i).demo for i in ids] == [True, True, False]
    for job_id in ids:
        token = client.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
        client.get(client.post("/fake-approve", data={"token": token}).headers["location"])
        client.app.state.runner.wait(job_id, timeout=60)
    shared = store.root.parent / "demo-cache"
    assert seen[0] == seen[1] == shared and seen[2] == store.dir(ids[2]) / "cache"


def test_mcp_links_follow_the_running_web_service(tmp_path, monkeypatch):
    """Regression: `serve --port 8799` + `mcp` handed out approval links on :8787."""
    from canonkeeper.preflight.mcp_server import PUBLIC_URL_FILE, public_url_for

    monkeypatch.delenv("PREFLIGHT_PUBLIC_URL", raising=False)
    assert public_url_for(tmp_path) == "http://127.0.0.1:8787"
    (tmp_path / PUBLIC_URL_FILE).write_text("http://127.0.0.1:8799/\n", encoding="utf-8")
    assert public_url_for(tmp_path) == "http://127.0.0.1:8799"
    monkeypatch.setenv("PREFLIGHT_PUBLIC_URL", "https://preflight.example")
    assert public_url_for(tmp_path) == "https://preflight.example"
    assert public_url_for(tmp_path, "https://flag.example/") == "https://flag.example"


@respx.mock
def test_a_capture_is_never_paid_on_an_amount_paypal_did_not_report():
    """Regression: `not o.amount` used to count as a match. A minimal capture reply is re-read
    from the order (which always carries amounts); with no amount anywhere, the job is not paid."""
    from canonkeeper.preflight.paypal import Order, capture_problem

    bare = Order(id="X", status="COMPLETED", approve_url=None, amount="", currency="", raw={})
    assert capture_problem(bare, amount="5.05", currency="USD") is not None

    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.post(f"{SANDBOX}/v2/checkout/orders/9AB/capture").mock(
        return_value=httpx.Response(201, json={"id": "9AB", "status": "COMPLETED"})
    )
    full = {
        "id": "9AB",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": "5.05"},
                "payments": {
                    "captures": [
                        {
                            "id": "CAP1",
                            "status": "COMPLETED",
                            "amount": {"currency_code": "USD", "value": "5.05"},
                        }
                    ]
                },
            }
        ],
    }
    respx.get(f"{SANDBOX}/v2/checkout/orders/9AB").mock(return_value=httpx.Response(200, json=full))
    o = PayPalClient("cid", "sec", mode="sandbox").capture_order("9AB")
    assert (o.amount, o.currency) == ("5.05", "USD")
    assert capture_problem(o, amount="5.05", currency="USD") is None
    assert capture_problem(o, amount="9.99", currency="USD") is not None


def test_home_page_says_what_it_is_what_it_costs_and_where_text_goes(client, tmp_path):
    """T3: one screen answers what it does, for whom, the price, and how a private book is kept
    private, and the privacy line matches what this instance actually does with text."""
    html = client.get("/").text
    assert 'data-testid="cta-demo"' in html and 'href="/sample"' in html
    assert "For authors of long-running series" in html and "$5 + $4 per 100,000 tokens" in html
    assert "about 73,000 words" in html  # authors count words, not tokens
    assert "in this demo nothing is sent to any model" in html  # mock provider
    assert "run Preflight on your own computer with a local model" in html
    # the privacy line sits above the upload form, where the decision to upload is made
    assert html.index('data-testid="privacy"') < html.index('action="/quote"')
    assert html.count('class="banner"') == 1  # one demo banner, not two stacked

    settings = Settings(
        provider="gemini", gemini_api_key="k", workdir=tmp_path / "g", _env_file=None
    )
    gemini = TestClient(create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pg"))
    page = gemini.get("/").text
    assert (
        "Public-domain books only on this site" in page and "do not upload an unpublished" in page
    )


def test_keep_awake_until_a_date_pings_while_idle(tmp_path, monkeypatch):
    """T4: through the judging period the service pings itself busy or not, because a free host
    that sleeps wipes its disk (jobs, reports, the demo cache); after the date it stops."""
    from datetime import UTC, datetime, timedelta

    import httpx

    from canonkeeper.preflight.app import keep_awake_until
    from canonkeeper.preflight.jobs import JobStore, Runner

    assert keep_awake_until("2026-12-16") == datetime(2026, 12, 17, tzinfo=UTC)
    assert keep_awake_until("") is None and keep_awake_until(None) is None
    with pytest.raises(RuntimeError, match="not a date"):
        keep_awake_until("mid-December")

    pings: list[str] = []
    monkeypatch.setattr(httpx, "get", lambda url, timeout: pings.append(url))
    runner = Runner(JobStore(tmp_path / "jobs"), Settings(provider="mock", _env_file=None))
    runner.keepalive_url, runner.keepalive_every = "http://x/healthz", 0.01
    runner.ensure_keepalive()
    assert runner._keepalive is None  # idle and no date: no reason to stay awake
    runner.keep_awake_until = datetime.now(UTC) + timedelta(seconds=0.3)
    runner.ensure_keepalive()
    time.sleep(0.15)
    assert pings and not runner.busy()
    time.sleep(0.4)
    assert runner._keepalive is not None and not runner._keepalive.is_alive()  # date passed


def test_an_order_the_days_quota_cannot_finish_is_refused_before_paypal(tmp_path):
    """T4: on a capped free tier, a buyer must not be charged for an audit the day's requests
    cannot finish. The refusal happens before any PayPal order exists, on the web and over MCP."""
    from datetime import UTC, datetime

    from canonkeeper.preflight.mcp_server import PreflightService

    settings = Settings(
        provider="gemini", gemini_api_key="k", workdir=tmp_path / "w", _env_file=None
    )
    pp = FakePayPal()
    web = TestClient(create_app(settings=settings, paypal=pp, workdir=tmp_path / "pf"))
    r = web.post("/quote-demo", follow_redirects=True)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]

    # A spent day on the extraction model and its fallback (the counter's own log format).
    now = datetime.now(UTC).isoformat()
    spent = {"ts": now, "ok": True, "latency_s": 1.0}
    lines = [
        json.dumps({**spent, "model": m})
        for m in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
        for _ in range(500)
    ]
    settings.request_log.parent.mkdir(parents=True, exist_ok=True)
    settings.request_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = web.post(f"/jobs/{job_id}/order", follow_redirects=False)
    assert r.status_code == 503 and 'data-testid="quota-short"' in r.text
    assert "nothing was charged" in r.text and "midnight US Pacific" in r.text
    assert web.app.state.store.get(job_id).order_id is None  # no PayPal order was made

    service = PreflightService(settings, pp, tmp_path / "pf", public_base_url="http://x")
    answer = service.order_audit(job_id)
    assert answer["ordered"] is False and "quota" in answer["reason"]

    settings.request_log.write_text(
        "", encoding="utf-8"
    )  # a fresh day: the same order goes through
    r = web.post(f"/jobs/{job_id}/order", follow_redirects=False)
    assert r.status_code == 303 and web.app.state.store.get(job_id).order_id


def _demo_job(web) -> str:
    return (
        web.post("/quote-demo", follow_redirects=True)
        .text.split('action="/jobs/')[1]
        .split("/order")[0]
    )


def test_paypal_buttons_appear_only_with_real_credentials(tmp_path, client):
    """T6: PayPal's own JS SDK buttons on the quote page in sandbox/live; the fake keeps its
    page; the plain redirect stays as a fallback for no-script and for agents."""

    class SandboxLike(FakePayPal):
        mode = "sandbox"
        client_id = "SB-CLIENT-ID"

    assert (
        'data-testid="paypal-buttons"' not in client.post("/quote-demo", follow_redirects=True).text
    )  # fake mode
    settings = Settings(provider="mock", workdir=tmp_path / "s", _env_file=None)
    web = TestClient(create_app(settings=settings, paypal=SandboxLike(), workdir=tmp_path / "spf"))
    page = web.post("/quote-demo", follow_redirects=True).text
    assert 'data-testid="paypal-buttons"' in page
    assert "https://www.paypal.com/sdk/js?client-id=SB-CLIENT-ID&currency=USD" in page
    assert "Or pay on PayPal&#39;s page" in page or "Or pay on PayPal's page" in page


def test_buttons_create_and_confirm_through_json(client):
    store = client.app.state.store
    job_id = _demo_job(client)
    hdr = {"Accept": "application/json"}
    first = client.post(f"/jobs/{job_id}/order", headers=hdr).json()
    again = client.post(f"/jobs/{job_id}/order", headers=hdr).json()
    assert first["order_id"].startswith("FAKE") and again == first  # the window reopened: reused
    early = client.post(f"/jobs/{job_id}/confirm", headers=hdr).json()
    assert early["ok"] is False and early["job_url"] == f"/jobs/{job_id}"
    client.app.state.paypal.approve(first["order_id"])  # the buyer approves in PayPal's window
    done = client.post(f"/jobs/{job_id}/confirm", headers=hdr).json()
    assert done == {"ok": True, "problem": None, "job_url": f"/jobs/{job_id}"}
    client.app.state.runner.wait(job_id, timeout=60)
    assert store.get(job_id).status == "done"
    late = client.post(f"/jobs/{job_id}/order", headers=hdr)
    assert late.status_code == 409 and late.json()["status"] == "done"


def _event(kind: str, order_id: str) -> dict:
    if kind == "CHECKOUT.ORDER.APPROVED":
        return {"event_type": kind, "resource": {"id": order_id}}
    related = {"supplementary_data": {"related_ids": {"order_id": order_id}}}
    return {"event_type": kind, "resource": {"id": "CAP", **related}}


def test_webhook_only_prompts_a_check_with_paypal(client):
    """A PayPal notice settles a job whose buyer approved and closed the tab; an unverified
    notice is refused; and even a verified notice about an unapproved order pays nothing,
    because settle asks PayPal and pays only on a completed capture."""
    store, pp = client.app.state.store, client.app.state.paypal
    signed = {"paypal-transmission-sig": FakePayPal.WEBHOOK_SIGNATURE}
    job_id = _demo_job(client)
    client.post(f"/jobs/{job_id}/order", follow_redirects=False)
    order_id = store.get(job_id).order_id

    approved = _event("CHECKOUT.ORDER.APPROVED", order_id)
    assert client.post("/paypal/webhook", json=approved).status_code == 400  # no signature
    r = client.post("/paypal/webhook", json=approved, headers=signed)
    assert r.json()["settled"] is False and store.get(job_id).status == "ordered"  # not approved

    pp.approve(order_id)  # approved in PayPal, then the buyer closed the tab
    r = client.post("/paypal/webhook", json=approved, headers=signed)
    assert r.json() == {"job": job_id, "settled": True, "problem": None}
    client.app.state.runner.wait(job_id, timeout=60)
    assert store.get(job_id).status == "done" and store.get(job_id).capture_id
    # The capture's own notice arriving afterwards changes nothing (settle is idempotent).
    r = client.post(
        "/paypal/webhook", json=_event("PAYMENT.CAPTURE.COMPLETED", order_id), headers=signed
    )
    assert r.status_code == 200 and store.get(job_id).status == "done"
    other = {"event_type": "PAYMENT.CAPTURE.DENIED", "resource": {}}
    assert client.post("/paypal/webhook", json=other, headers=signed).json() == {
        "ignored": "PAYMENT.CAPTURE.DENIED"
    }
    unknown = _event("CHECKOUT.ORDER.APPROVED", "NOPE")
    assert client.post("/paypal/webhook", json=unknown, headers=signed).json() == {
        "ignored": "unknown order"
    }


def test_webhooks_refused_in_sandbox_without_a_webhook_id(tmp_path, monkeypatch):
    class SandboxLike(FakePayPal):
        mode = "sandbox"

    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    settings = Settings(provider="mock", workdir=tmp_path / "s", _env_file=None)
    web = TestClient(create_app(settings=settings, paypal=SandboxLike(), workdir=tmp_path / "spf"))
    assert web.post("/paypal/webhook", json={}).status_code == 503


@respx.mock
def test_real_client_verifies_webhooks_with_paypal():
    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    route = respx.post(f"{SANDBOX}/v1/notifications/verify-webhook-signature")
    pp = PayPalClient("cid", "sec", mode="sandbox")
    headers = {"PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-ID": "t1"}
    body = json.dumps({"event_type": "CHECKOUT.ORDER.APPROVED"}).encode()
    route.mock(return_value=httpx.Response(200, json={"verification_status": "SUCCESS"}))
    assert pp.verify_webhook(headers, body, "WH-1") is True
    sent = json.loads(route.calls.last.request.content)
    assert sent["webhook_id"] == "WH-1" and sent["transmission_sig"] == "sig"
    assert sent["webhook_event"]["event_type"] == "CHECKOUT.ORDER.APPROVED"
    route.mock(return_value=httpx.Response(200, json={"verification_status": "FAILURE"}))
    assert pp.verify_webhook(headers, body, "WH-1") is False
    route.mock(return_value=httpx.Response(500))
    assert pp.verify_webhook(headers, body, "WH-1") is False
    assert pp.verify_webhook(headers, b"not json", "WH-1") is False


def test_job_page_carries_a_payment_receipt(client):
    store = client.app.state.store
    job_id = _demo_job(client)
    client.post(f"/jobs/{job_id}/order", follow_redirects=False)
    page = client.get(f"/jobs/{job_id}").text
    assert 'data-testid="receipt"' in page and "Nothing yet" in page and "simulated" in page
    order_id = store.get(job_id).order_id
    client.app.state.paypal.approve(order_id)
    client.post(f"/jobs/{job_id}/confirm")
    client.app.state.runner.wait(job_id, timeout=60)
    page = client.get(f"/jobs/{job_id}").text
    assert store.get(job_id).capture_id in page and order_id in page and "Nothing yet" not in page


def test_the_bible_explorer_is_served_with_its_own_grid_script(client):
    """T15: the bible as an AG Grid (Community, MIT), linked from the job page, served with the
    vendored script (no CDN needed when hosted), with the same rows as the CSV."""
    import re

    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    token = client.post(f"/jobs/{job_id}/order").headers["location"].split("token=")[1]
    client.get(client.post("/fake-approve", data={"token": token}).headers["location"])
    client.app.state.runner.wait(job_id, timeout=60)
    assert f'href="/jobs/{job_id}/bible-grid.html"' in client.get(f"/jobs/{job_id}").text
    grid = client.get(f"/jobs/{job_id}/bible-grid.html").text
    rows = json.loads(
        re.search(r'<script id="rows" type="application/json">(.*?)</script>', grid, re.S)[1]
    )
    csv_rows = client.get(f"/jobs/{job_id}/bible.csv").content.decode("utf-8-sig").splitlines()[1:]
    assert len(rows) == len(csv_rows) and f"{len(rows):,} sourced facts" in grid
    assert any(r["entity"] == "Ivy Marlowe" and r["disputed"] for r in rows)
    assert client.get(f"/jobs/{job_id}/bible.html").status_code == 200  # the grid's relative links
    assert client.get(f"/jobs/{job_id}/download/bible-grid.html").status_code == 200
    script = client.get("/static/ag-grid-community-36.2.0.min.js")
    assert script.status_code == 200 and "createGrid" in script.text
    assert client.get("/static/AG-GRID-LICENSE.txt").text.startswith("The MIT License")
    sample = client.get("/sample/bible")
    assert (
        sample.status_code == 200 and "3,963 sourced facts" in sample.text
    )  # every bible fact (an alias cycle once hid 54)


def test_a_passage_cannot_break_out_of_the_grid_page(tmp_path):
    from canonkeeper.models import Claim, ClaimKind, Provenance
    from canonkeeper.report.bible import EntityView, SlotView, write_bible_grid

    claim = Claim(
        kind=ClaimKind.ATTRIBUTE,
        subject="Ann",
        predicate="eye_color",
        object="green",
        provenance=Provenance(
            book="01", book_index=0, chapter="I", chapter_index=0,
            quote='her eyes</script><script>alert(1)</script> were green',
        ),
    )  # fmt: skip
    view = EntityView("Ann", [], 1, "01", [SlotView("eye_color", [("green", [claim])])])
    html = write_bible_grid([view], "S", tmp_path / "g.html", n_claims=1).read_text()
    assert "<script>alert(1)" not in html and "\\u003c/script\\u003e" in html
