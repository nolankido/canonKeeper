"""Judge review: a budget had no limit per purchase and no end date. The person sets either when
the assistant opens it (or later on the budget page); limits only ever tighten."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


@pytest.fixture
def shop(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    return TestClient(app, follow_redirects=False), app.state.mcp_service


def _funded(svc, amount="20", **limits):
    b = svc.open_budget(amount, **limits)
    svc.paypal.approve(svc.budgets.get(b["budget_id"]).order_id)
    assert svc.confirm_budget(b["budget_id"])["status"] == "active"
    return b


def _quoted(svc):
    return svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]


def test_no_single_purchase_above_the_persons_limit(shop):
    web, svc = shop
    b = _funded(svc, "20", max_per_purchase_usd="5")
    assert b["max_per_purchase"] == "5.00"
    job = _quoted(svc)  # $5.05
    refused = svc.pay_with_budget(job, b["budget_key"])
    assert refused["paid"] is False and "over the person's limit of $5.00" in refused["reason"]
    assert svc.store.get(job).status == "quoted" and svc.budgets.get(b["budget_id"]).spends == []
    page = web.get(f"/budget/{b['budget_id']}").text
    assert "at most $5.00 per purchase" in page and "no end date" in page


def test_a_budget_stops_spending_on_its_end_date_or_when_the_person_says(shop):
    web, svc = shop
    b = _funded(svc, "20", days=30)
    assert b["spend_until"]
    job = _quoted(svc)
    assert svc.pay_with_budget(job, b["budget_key"])["paid"] is True
    assert web.post(f"/budget/{b['budget_id']}/limits", data={"stop_now": "yes"}).status_code == 303
    refused = svc.pay_with_budget(_quoted(svc), b["budget_key"])
    assert refused["paid"] is False and "stopped spending" in refused["reason"]
    assert "close it" in refused["reason"]
    assert "spending ended" in web.get(f"/budget/{b['budget_id']}").text
    assert svc.close_budget(b["budget_key"])["status"] == "closed"  # the rest still goes back


def test_limits_only_tighten(shop):
    web, svc = shop
    b = _funded(svc, "20")
    url = f"/budget/{b['budget_id']}/limits"
    assert web.post(url, data={"max_per_job": "8"}).status_code == 303
    assert svc.budgets.get(b["budget_id"]).max_per_job_usd == "8.00"
    page = web.post(url, data={"max_per_job": "12"})
    assert page.status_code == 200 and "a limit can only go down (it is $8.00)" in page.text
    assert "the limit must be a number" in web.post(url, data={"max_per_job": "lots"}).text
    assert svc.budgets.get(b["budget_id"]).max_per_job_usd == "8.00"


def test_limits_are_checked_when_the_budget_is_opened(shop):
    _web, svc = shop
    with pytest.raises(ValueError, match="between \\$1 and the budget's amount"):
        svc.open_budget("10", max_per_purchase_usd="50")
    with pytest.raises(ValueError, match="between 1 and 365 days"):
        svc.open_budget("10", days=400)
    assert Decimal(svc.open_budget("10", max_per_purchase_usd=3)["max_per_purchase"]) == 3
