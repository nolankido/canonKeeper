"""Review: settle captures before claiming the job, so two returns for two orders of one job
(the replaced order approved in an old tab, the new one in another) capture both."""

from __future__ import annotations

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.payments import settle
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


class Interleaved(FakePayPal):
    """Runs `during` once, while the first capture is on its way to PayPal."""

    during = None

    def capture_order(self, order_id):
        hook, self.during = self.during, None
        if hook:
            hook()
        return super().capture_order(order_id)


def test_two_returns_for_two_orders_of_one_job_charge_once(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    pp = Interleaved()
    svc = PreflightService(settings, pp, tmp_path / "pf", public_base_url="http://x")
    store, runner = svc.store, svc.runner
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    first = svc.order_audit(job)["order_id"]
    store.update(job, expect=("ordered",), status="cancelled")  # GET /cancel?job=...
    second = svc.order_audit(job)["order_id"]  # first not approved yet, so a new order
    assert first != second
    pp.approve(first)  # the old tab
    pp.approve(second)  # the new tab

    def second_return():  # GET /return?token=<second>, at the same moment
        settle(store.get(job), pp, store, runner, known_approved=True, order_id_hint=second)

    pp.during = second_return
    settle(store.get(job), pp, store, runner, known_approved=True, order_id_hint=first)
    runner.wait(job, timeout=60)

    completed = [o for o in (first, second) if pp.get_order(o).status == "COMPLETED"]
    assert len(completed) == 1, f"both orders captured: {completed}; job {store.get(job).status}"
