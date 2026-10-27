"""Review: a guarantee refund PayPal made but never answered is not recorded, so the cap is
re-evaluated on the retry and the person gets more than half the price back."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


class DropsOneAnswer(FakePayPal):
    drop: str | None = None  # the request id whose answer is lost after PayPal refunded

    def refund_capture(self, capture_id, amount=None, request_id=None):
        r = super().refund_capture(capture_id, amount, request_id)
        if request_id is not None and request_id == self.drop:
            self.drop = None
            raise httpx.ReadTimeout("no answer")
        return r


def test_a_timed_out_guarantee_refund_cannot_push_past_half_the_price(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    pp = DropsOneAnswer()
    app = create_app(settings=settings, paypal=pp, workdir=tmp_path / "pf")
    svc = app.state.mcp_service
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    assert svc.store.get(job).status == "done"
    report = svc.store.dir(job) / "reports" / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    base = next(f for f in data["findings"] if f["verdict"] == "contradiction")
    data["findings"] = [base] * 7  # seven contradictions; the $5.05 cap is $2.52
    report.write_text(json.dumps(data), encoding="utf-8")

    for i in range(4):
        assert svc.mark_finding_wrong(job, i)["given_back"] == "0.50 USD"  # 2.00 so far
    pp.drop = f"guarantee-{job}-4"
    with pytest.raises(httpx.ReadTimeout):  # PayPal refunded 0.50; the answer was lost
        svc.mark_finding_wrong(job, 4)
    svc.mark_finding_wrong(job, 5)  # 2.00 recorded + 0.50 <= 2.52: refunded
    svc.mark_finding_wrong(job, 4)  # the retry: now over the cap, records 0.00

    order = pp.get_order(svc.store.get(job).order_id)
    at_paypal = sum(Decimal(v[1]) for v in order.raw["partial_refunds"].values())
    assert at_paypal <= Decimal("2.52"), (at_paypal, svc.store.get(job).guarantee)
