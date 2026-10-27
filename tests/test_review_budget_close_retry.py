"""Review: a close whose refund PayPal made but never answered leaves the budget spendable."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


class SlowRefunds(FakePayPal):
    """PayPal makes the first partial refund, but the answer never arrives (a read timeout)."""

    timed_out = False

    def refund_capture(self, capture_id, amount=None, request_id=None):
        r = super().refund_capture(capture_id, amount, request_id)
        if amount is not None and not self.timed_out:
            self.timed_out = True
            raise httpx.ReadTimeout("no answer")
        return r


def test_a_close_that_timed_out_after_paypal_refunded_cannot_be_overspent(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    pp = SlowRefunds()
    svc = PreflightService(settings, pp, tmp_path / "pf", public_base_url="http://x")
    b = svc.open_budget("20")
    pp.approve(svc.budgets.get(b["budget_id"]).order_id)
    assert svc.confirm_budget(b["budget_id"])["status"] == "active"

    with pytest.raises(httpx.ReadTimeout):  # PayPal refunded the whole 20.00, then timed out
        svc.close_budget(b["budget_key"])

    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    spent = svc.pay_with_budget(job, b["budget_key"])  # the budget is still "active"
    svc.close_budget(b["budget_key"])  # retry: same request id, PayPal replays the 20.00 refund

    budget = svc.budgets.get(b["budget_id"])
    order = pp._orders[budget.order_id]
    refunded_at_paypal = sum(Decimal(v[1]) for v in order.raw["partial_refunds"].values())
    spent_on_audits = budget.spent - budget.credited
    # funded 20.00: what went back to PayPal plus what audits consumed can never exceed it
    assert refunded_at_paypal + spent_on_audits <= Decimal("20.00"), (
        spent,
        refunded_at_paypal,
        budget.refunds,
    )
