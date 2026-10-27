"""T8: agent budgets. The person funds a balance through PayPal once; the assistant spends it on
audits, never past it; failed audits are credited back; closing refunds the rest to PayPal."""

from __future__ import annotations

import json
import threading
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.payments import refund
from canonkeeper.preflight.paypal import SANDBOX, FakePayPal, PayPalClient

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


@pytest.fixture
def svc(tmp_path) -> PreflightService:
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    return PreflightService(settings, FakePayPal(), tmp_path / "pf", public_base_url="http://x")


def _funded(svc: PreflightService, amount: str) -> dict:
    opened = svc.open_budget(amount)
    assert svc.confirm_budget(opened["budget_id"])["problem"] == "not approved yet"
    order_id = svc.budgets.get(opened["budget_id"]).order_id
    svc.paypal.approve(order_id)  # the PERSON approves on PayPal
    assert svc.confirm_budget(opened["budget_id"])["status"] == "active"
    return opened


def _quoted(svc: PreflightService) -> str:
    return svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]


def test_an_assistant_spends_a_budget_the_person_funded_once(svc):
    b = _funded(svc, "20")
    assert b["approve_url"].startswith("http://x/fake-approve") and b["budget_key"].startswith(
        "pfk_"
    )
    job = _quoted(svc)
    paid = svc.pay_with_budget(job, b["budget_key"])
    assert paid["paid"] is True and paid["budget_left"] == "14.95"  # 20.00 - 5.05
    svc.runner.wait(job, timeout=60)
    assert svc.store.get(job).status == "done" and svc.store.get(job).budget_id == b["budget_id"]
    status = svc.budget_status(b["budget_key"])
    assert status["balance_usd"] == "14.95" and status["spends"][0]["job_id"] == job
    assert "key_hash" not in status and b["budget_key"] not in json.dumps(status)


def test_a_budget_is_never_overdrawn_or_spent_before_it_is_funded(svc):
    unfunded = svc.open_budget("10")
    job = _quoted(svc)
    assert svc.pay_with_budget(job, unfunded["budget_key"])["reason"] == "this budget is ordered"
    small = _funded(svc, "5")
    refused = svc.pay_with_budget(job, small["budget_key"])
    assert refused["paid"] is False and "$5.00 left; this costs $5.05" in refused["reason"]
    assert svc.pay_with_budget(job, "pfk_not-a-key")["reason"] == "unknown budget key"
    assert svc.store.get(job).status == "quoted"
    with pytest.raises(ValueError):
        svc.open_budget("500")


def test_two_spends_at_once_cannot_both_take_the_last_money(svc):
    b = _funded(svc, "6")  # enough for one 5.05 audit, not two
    jobs = [_quoted(svc), _quoted(svc)]
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda j=j: results.append(svc.pay_with_budget(j, b["budget_key"])))
        for j in jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["paid"] for r in results) == [False, True]
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "0.95"
    for j in jobs:
        svc.runner.wait(j, timeout=60)


def test_a_budget_replaces_an_unapproved_paypal_order_and_that_order_never_charges(svc):
    """One payment per job, whichever way it comes. The person clicked "Pay with PayPal" and
    left; the assistant pays from the budget instead: the unapproved order is voided, and if
    the person approves it later, nothing is captured."""
    from canonkeeper.preflight.payments import settle

    b = _funded(svc, "20")
    job = _quoted(svc)
    first = svc.order_audit(job)["order_id"]  # a PayPal order exists, never approved
    paid = svc.pay_with_budget(job, b["budget_key"])
    assert paid["paid"] is True and "replaced_order" in paid
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "14.95"
    svc.paypal.approve(first)  # the person approves the old order in another tab, too late
    assert settle(svc.store.get(job), svc.paypal, svc.store, svc.runner, known_approved=True,
                  order_id_hint=first) is None  # fmt: skip
    assert svc.paypal.get_order(first).status != "COMPLETED"  # never captured
    svc.runner.wait(job, timeout=60)


def test_a_job_whose_paypal_order_is_approved_is_not_paid_from_a_budget(svc):
    b = _funded(svc, "20")
    job = _quoted(svc)
    order = svc.order_audit(job)["order_id"]
    svc.paypal.approve(order)  # approved: PayPal's payment is the one that counts
    refused = svc.pay_with_budget(job, b["budget_key"])
    assert refused["paid"] is False and "PayPal order of its own" in refused["reason"]
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "20.00"


def test_failed_audits_are_credited_back_and_closing_refunds_the_rest(svc):
    b = _funded(svc, "20")
    first, second = _quoted(svc), _quoted(svc)
    for j in (first, second):
        assert svc.pay_with_budget(j, b["budget_key"])["paid"]
        svc.runner.wait(j, timeout=60)
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "9.90"

    svc.store.update(first, status="failed", error="provider overloaded")  # as if it had failed
    assert refund(svc.store.get(first), svc.paypal, svc.store, svc.budgets) is None
    assert svc.store.get(first).status == "refunded"
    assert svc.store.get(first).refund_id == f"budget:{b['budget_id']}"
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "14.95"  # back in the budget
    assert refund(svc.store.get(first), svc.paypal, svc.store, svc.budgets) is None  # once only
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "14.95"

    closed = svc.close_budget(b["budget_key"])
    assert closed["status"] == "closed" and closed["balance_usd"] == "0.00"
    assert closed["refunds"][0]["amount"] == "14.95"  # the unused rest, back to PayPal
    assert svc.close_budget(b["budget_key"])["refunds"] == closed["refunds"]  # idempotent
    third = _quoted(svc)
    assert svc.pay_with_budget(third, b["budget_key"])["reason"] == "this budget is closed"

    # A job paid from the budget that fails after it was closed is refunded to PayPal directly.
    svc.store.update(second, status="failed", error="x")
    assert refund(svc.store.get(second), svc.paypal, svc.store, svc.budgets) is None
    after = svc.budget_status(b["budget_key"])
    assert after["balance_usd"] == "0.00" and [r["amount"] for r in after["refunds"]] == [
        "14.95",
        "5.05",
    ]
    capture = svc.budgets.get(b["budget_id"]).capture_id
    order = next(o for o in svc.paypal._orders.values() if o.raw.get("purchase_units") and
                 o.raw["purchase_units"][0]["payments"]["captures"][0]["id"] == capture)  # fmt: skip
    assert sum(Decimal(v[1]) for v in order.raw["partial_refunds"].values()) == Decimal("20.00")


def test_the_budget_page_shows_every_movement_and_closes(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    web = TestClient(app)
    svc = app.state.mcp_service
    b = svc.open_budget("10")
    page = web.get(f"/budget/{b['budget_id']}").text
    assert "Approve on PayPal" in page and "no-referrer" in page
    # The person approves on the (fake) PayPal page; PayPal sends them back to the budget.
    order_id = svc.budgets.get(b["budget_id"]).order_id
    r = web.post("/fake-approve", data={"token": order_id}, follow_redirects=False)
    assert (
        r.headers["location"].startswith(f"http://x/budget/{b['budget_id']}/return")
        or "/budget/" in r.headers["location"]
    )
    web.get(f"/budget/{b['budget_id']}/return", follow_redirects=False)
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    assert svc.pay_with_budget(job, b["budget_key"])["paid"]
    svc.runner.wait(job, timeout=60)
    page = web.get(f"/budget/{b['budget_id']}").text
    assert 'data-testid="budget-balance">$4.95' in page and f'href="/jobs/{job}"' in page
    assert b["budget_key"] not in page  # the page shows the budget, never the spending key
    assert web.post(f"/budget/{b['budget_id']}/close", data={}).status_code == 400  # unconfirmed
    web.post(f"/budget/{b['budget_id']}/close", data={"confirm": "yes"})
    closed = web.get(f"/budget/{b['budget_id']}").text
    assert "Refunded to PayPal" in closed and "$4.95 went back to your PayPal account" in closed
    assert web.get("/budget/not-a-budget-id-at-all").status_code == 404


def test_a_webhook_funds_a_budget_whose_person_closed_the_tab(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    web = TestClient(app)
    svc = app.state.mcp_service
    b = svc.open_budget("10")
    order_id = svc.budgets.get(b["budget_id"]).order_id
    svc.paypal.approve(order_id)
    event = {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order_id}}
    signed = {"paypal-transmission-sig": FakePayPal.WEBHOOK_SIGNATURE}
    r = web.post("/paypal/webhook", json=event, headers=signed).json()
    assert r == {"budget": b["budget_id"], "settled": True, "problem": None}
    assert svc.budgets.get(b["budget_id"]).status == "active"


@respx.mock
def test_real_client_sends_a_partial_refund_with_its_own_request_id():
    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    route = respx.post(f"{SANDBOX}/v2/payments/captures/CAP1/refund").mock(
        return_value=httpx.Response(201, json={"id": "R1", "status": "COMPLETED"})
    )
    r = PayPalClient("cid", "sec", mode="sandbox").refund_capture("CAP1", "14.95", "budget-b-close")
    assert r.status == "COMPLETED"
    sent = route.calls.last.request
    assert json.loads(sent.content) == {"amount": {"value": "14.95", "currency_code": "USD"}}
    assert sent.headers["PayPal-Request-Id"] == "budget-b-close"


def test_a_budget_paid_job_shows_where_its_money_came_from_and_went(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    web, svc = TestClient(app), app.state.mcp_service
    b = _funded(svc, "20")
    job = _quoted(svc)
    svc.pay_with_budget(job, b["budget_key"])
    svc.runner.wait(job, timeout=60)
    page = web.get(f"/jobs/{job}").text
    assert "paid by your AI assistant" in page and f'href="/budget/{b["budget_id"]}"' in page
    svc.store.update(job, status="failed", error="x")
    page = web.get(f"/jobs/{job}").text
    assert "goes back into your assistant" in page
    web.post(f"/jobs/{job}/refund", data={"confirm": "yes"})
    assert "went back to <a" in web.get(f"/jobs/{job}").text
