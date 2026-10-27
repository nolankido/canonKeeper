"""Review: close_budget holds the budget's file lock across PayPal calls (30 s timeout each, plus a
token fetch), but filelock treats any lock older than 30 s as abandoned. A second process (the
stdio `canonkeeper mcp` beside `serve`) then spends under the "same" lock, and close writes its
stale copy over that spend: the job is paid from the budget, and the whole balance is refunded."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]

OTHER_PROCESS = """
import sys
from pathlib import Path
from canonkeeper.config import Settings
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.paypal import FakePayPal
w, pf, job, key = sys.argv[1:]
s = Settings(provider="mock", workdir=Path(w), _env_file=None)
svc = PreflightService(s, FakePayPal(), Path(pf), public_base_url="http://x")
print(svc.pay_with_budget(job, key)["paid"])
"""


class SlowRefund(FakePayPal):
    """PayPal takes 31 s to answer the refund (simulated by aging the lock file), and meanwhile
    the other process spends."""

    def __init__(self, lock: Path, spend) -> None:
        super().__init__()
        self.lock_path, self.spend = lock, spend

    def refund_capture(self, capture_id, amount=None, request_id=None):
        old = time.time() - 31
        if self.lock_path.exists():  # a lock held across this call would now look abandoned
            os.utime(self.lock_path, (old, old))
        self.spend()
        return super().refund_capture(capture_id, amount, request_id)


def test_a_slow_paypal_call_under_the_budget_lock_never_loses_a_spend(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    out: list[str] = []
    box: dict = {}

    def spend_elsewhere() -> None:
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
        r = subprocess.run(
            [sys.executable, "-c", OTHER_PROCESS, str(tmp_path / "w"), str(tmp_path / "pf"),
             box["job"], box["key"]],
            capture_output=True, text=True, timeout=120, env=env,
        )  # fmt: skip
        out.append(r.stdout.strip() + r.stderr[-300:])

    svc = PreflightService(settings, FakePayPal(), tmp_path / "pf", public_base_url="http://x")
    b = svc.open_budget("20")
    svc.paypal.approve(svc.budgets.get(b["budget_id"]).order_id)
    assert svc.confirm_budget(b["budget_id"])["status"] == "active"
    box["job"] = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    box["key"] = b["budget_key"]
    lock = svc.budgets._path(b["budget_id"]).with_suffix(".lock")
    slow = SlowRefund(lock, spend_elsewhere)
    slow._orders = svc.paypal._orders  # the same PayPal account
    svc.paypal = slow

    svc.close_budget(b["budget_key"])

    budget = svc.budgets.get(b["budget_id"])
    job = svc.store.get(box["job"])
    assert len(out) == 1
    # Either the spend was refused (close had already set the balance aside for the refund) or
    # it was recorded; never a job paid from the budget with no spend on the budget.
    if job.status == "paid":
        assert job.budget_id == budget.id
        assert any(s["job_id"] == job.id for s in budget.spends), (budget.spends, budget.refunds)
    else:
        assert out == ["False"] and job.budget_id is None
    assert budget.spent + budget.refunded <= Decimal(budget.amount_usd) + budget.credited
