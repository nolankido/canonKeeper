"""Review: close_budget cancels an "ordered" budget without asking PayPal or re-reading it under
the lock, so a captured (held, or concurrently funded) budget ends "cancelled" with the money kept."""

from __future__ import annotations

import copy
from decimal import Decimal

from canonkeeper.config import Settings
from canonkeeper.preflight.mcp_server import PreflightService
from canonkeeper.preflight.payments import close_budget
from canonkeeper.preflight.paypal import FakePayPal, capture_id_of


def _svc(tmp_path, pp) -> PreflightService:
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    return PreflightService(settings, pp, tmp_path / "pf", public_base_url="http://x")


class Holding(FakePayPal):
    """The capture is made but PayPal holds it (an eCheck) until `clear()`."""

    held = True

    def capture_order(self, order_id):
        o = copy.deepcopy(super().capture_order(order_id))
        if self.held and o.raw.get("purchase_units"):
            o.raw["purchase_units"][0]["payments"]["captures"][0]["status"] = "PENDING"
        return o


def test_a_held_funding_capture_is_never_cancelled_away(tmp_path):
    pp = Holding()
    svc = _svc(tmp_path, pp)
    b = svc.open_budget("20")
    order_id = svc.budgets.get(b["budget_id"]).order_id
    pp.approve(order_id)
    assert "holding" in svc.confirm_budget(b["budget_id"])["problem"]  # captured, held
    svc.close_budget(b["budget_key"])  # the person closes the budget page's "ordered" budget
    pp.held = False  # PayPal clears the eCheck: the 20.00 is ours
    after = svc.confirm_budget(b["budget_id"])
    budget = svc.budgets.get(b["budget_id"])
    captured = capture_id_of(pp._orders[order_id]) is not None
    assert captured
    # money PayPal holds for this budget must be spendable or refunded, never just kept
    assert budget.status in ("active", "closed") or budget.refunded == Decimal("20.00"), after


def test_closing_with_a_stale_copy_never_cancels_a_funded_budget(tmp_path):
    pp = FakePayPal()
    svc = _svc(tmp_path, pp)
    b = svc.open_budget("20")
    seen = svc.budgets.get(b["budget_id"])  # close read it while still "ordered"...
    pp.approve(seen.order_id)
    assert svc.confirm_budget(b["budget_id"])["status"] == "active"  # ...funded meanwhile
    close_budget(seen, pp, svc.budgets)
    budget = svc.budgets.get(b["budget_id"])
    assert budget.status in ("active", "closed") and budget.balance + budget.refunded == Decimal(
        "20.00"
    ), (budget.status, budget.balance, budget.refunds)
