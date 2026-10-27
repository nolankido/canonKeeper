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
    # Not while a payment is in flight: the return would capture and run on missing files.
    assert client.post(f"/jobs/{job_id}/delete", data={"confirm": "yes"}).status_code == 409


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
    assert ">The Saltmarsh Chronicles</a>" in client.get("/").text  # demo mode: one user

    class SandboxLike(FakePayPal):
        mode = "sandbox"

    settings = Settings(provider="mock", workdir=tmp_path / "w2", _env_file=None)
    public = TestClient(
        create_app(settings=settings, paypal=SandboxLike(), workdir=tmp_path / "pf")
    )
    assert ">The Saltmarsh Chronicles</a>" not in public.get("/").text  # same store, unlisted


def test_demo_series_button_quotes_saltmarsh(client):
    assert 'action="/quote-demo"' in client.get("/").text
    r = client.post("/quote-demo")
    assert r.status_code == 200 and "The Saltmarsh Chronicles" in r.text and "Pay $" in r.text
    job = client.app.state.store.all()[0]
    assert job.books == ["01-the-harbour.txt", "02-the-wreck.txt", "03-the-return.txt"]


def test_free_tier_privacy_banner(tmp_path):
    settings = Settings(
        provider="gemini", gemini_api_key="x", workdir=tmp_path / "w", _env_file=None
    )
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    assert "never an unpublished manuscript" in TestClient(app).get("/").text
    local = Settings(provider="openweights", workdir=tmp_path / "w2", _env_file=None)
    app2 = create_app(settings=local, paypal=FakePayPal(), workdir=tmp_path / "pf2")
    assert "never an unpublished manuscript" not in TestClient(app2).get("/").text


def test_job_progress_is_recorded_while_running(client):
    """The runner records stage and progress so the job page and audit_status show it."""
    r = _upload(client)
    job_id = r.text.split('action="/jobs/')[1].split("/order")[0]
    store, runner = client.app.state.store, client.app.state.runner
    job = store.get(job_id)
    seen: list[tuple[str, int, int]] = []
    tick = runner._progress_writer(job)
    for args in [("extracting", 0, 3), ("extracting", 3, 3), ("judging", 0, 5), ("judging", 5, 5)]:
        tick(*args)
        j = store.get(job_id)
        seen.append((j.stage, j.progress_done, j.progress_total))
    assert seen[1] == ("extracting", 3, 3) and seen[-1] == ("judging", 5, 5)
    job = store.get(job_id)
    job.status = "running"
    store.save(job)
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
    r = client.post("/quote", data={"series_name": "The Saltmarsh Chronicles"}, files=files)
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
        f"/jobs/{audit_id}/check", files={"draft": ("04-the-ledger.txt", DRAFT4.encode())}
    )
    assert r.status_code == 200 and "Check <code>the-ledger.txt</code>" in r.text
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
