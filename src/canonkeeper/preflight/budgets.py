"""Agent budgets: the person funds a balance through PayPal once; their assistant spends it.

An assistant that has to send its person a PayPal link for every draft check is a messenger, not an
agent. A budget lets it act within a limit the person chose:

1. **Funding** is an ordinary PayPal order for the budget's amount. The person approves it on PayPal,
   and nothing is spendable until PayPal confirms a completed capture of exactly that amount (the
   same `capture_problem` rule a job is paid by).
2. **Spending** (`spend`) happens under the budget's lock: an audit or check is paid from the budget
   only if the balance covers its quote, so a budget is never overdrawn, even by two assistants at
   once. Every spend is on the budget's page.
3. **Failed audits** paid from a budget are credited back to it, not refunded through PayPal; once
   the budget is closed, such a credit goes straight back to PayPal instead.
4. **Closing** refunds the unused balance to the person's PayPal account as a partial refund of the
   funding capture, and stops all spending.

Two capabilities, kept apart: the budget id lets someone *see* the budget (its page), the spending
key lets someone *spend* it. The key is stored only as a hash.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import secrets
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from canonkeeper.preflight.filelock import file_lock, read_json, write_json

MIN_USD, MAX_USD = Decimal("5"), Decimal("200")
MAX_DAYS = 365
_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Budget:
    id: str
    key_hash: str
    amount_usd: str
    status: str = "ordered"  # ordered | active | closing | closed | cancelled
    # Limits the person sets: no single purchase above this, and no spending after this time.
    # Either can be tightened on the budget's page at any time, never loosened.
    max_per_job_usd: str | None = None
    spend_until: str | None = None
    close_requested: bool = False  # the person asked to close: refund whatever arrives
    order_id: str | None = None
    approve_url: str | None = None
    capture_id: str | None = None
    funded_at: str | None = None
    spends: list[dict[str, str]] = field(default_factory=list)  # job_id, amount, at, kind
    credits: list[dict[str, str]] = field(default_factory=list)  # job_id, amount, at, reason
    # refund_id, amount, at, status (SENDING until PayPal answers: it already counts against the
    # balance, so nothing can be spent from money on its way back), request_id, for
    refunds: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @property
    def spent(self) -> Decimal:
        return sum((Decimal(s["amount"]) for s in self.spends), Decimal(0))

    @property
    def credited(self) -> Decimal:
        return sum((Decimal(c["amount"]) for c in self.credits), Decimal(0))

    @property
    def refunded(self) -> Decimal:
        return sum((Decimal(r["amount"]) for r in self.refunds), Decimal(0))

    @property
    def balance(self) -> Decimal:
        """What is left to spend (or to give back): funded - spent + credited - refunded."""
        if self.status not in ("active", "closing", "closed"):
            return Decimal(0)
        return Decimal(self.amount_usd) - self.spent + self.credited - self.refunded

    def refuses(self, price: Decimal, now: datetime | None = None) -> str | None:
        """Why the person's limits forbid this purchase, or None."""
        if self.spend_until and (now or datetime.now(UTC)) >= datetime.fromisoformat(
            self.spend_until
        ):
            return (
                f"this budget stopped spending on {self.spend_until[:10]}, as the person set it; "
                "close it to send the rest back to PayPal"
            )
        if self.max_per_job_usd and price > Decimal(self.max_per_job_usd):
            return (
                f"this costs ${price:.2f}, over the person's limit of ${self.max_per_job_usd} "
                "per purchase: ask them"
            )
        return None

    def public(self) -> dict[str, Any]:
        """What the budget's page and an assistant may see (never the key or its hash)."""
        d = asdict(self)
        d.pop("key_hash")
        d["balance_usd"] = f"{self.balance:.2f}"
        d["spent_usd"] = f"{self.spent:.2f}"
        return d


class BudgetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, budget_id: str) -> Path:
        if not _ID.fullmatch(budget_id):
            raise KeyError(budget_id)
        return self.root / f"{budget_id}.json"

    def create(
        self,
        amount_usd: Decimal,
        *,
        max_per_job_usd: Decimal | None = None,
        days: int | None = None,
    ) -> tuple[Budget, str]:
        """A new, unfunded budget and its spending key (returned once, stored as a hash)."""
        if not MIN_USD <= amount_usd <= MAX_USD:
            raise ValueError(f"a budget is between ${MIN_USD} and ${MAX_USD}")
        if max_per_job_usd is not None and not Decimal("1") <= max_per_job_usd <= amount_usd:
            raise ValueError("the limit per purchase is between $1 and the budget's amount")
        if days is not None and not 1 <= days <= MAX_DAYS:
            raise ValueError(f"a budget can last between 1 and {MAX_DAYS} days")
        key = "pfk_" + secrets.token_urlsafe(24)
        budget = Budget(
            id=secrets.token_urlsafe(12),
            key_hash=key_hash(key),
            amount_usd=f"{amount_usd:.2f}",
            max_per_job_usd=f"{max_per_job_usd:.2f}" if max_per_job_usd is not None else None,
            spend_until=(
                (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
                if days is not None
                else None
            ),
        )
        self._write(budget)
        return budget, key

    def get(self, budget_id: str) -> Budget | None:
        try:
            data = read_json(self._path(budget_id))
        except (KeyError, OSError, ValueError):
            return None
        return Budget(**data) if data is not None else None

    def by_key(self, key: str) -> Budget | None:
        wanted = key_hash(key)
        for p in self.root.glob("*.json"):
            with contextlib.suppress(OSError, ValueError):
                data = read_json(p) or {}
                if secrets.compare_digest(str(data.get("key_hash", "")), wanted):
                    return Budget(**data)
        return None

    def by_order(self, order_id: str) -> Budget | None:
        for p in self.root.glob("*.json"):
            with contextlib.suppress(OSError, ValueError):
                data = read_json(p) or {}
                if data.get("order_id") == order_id:
                    return Budget(**data)
        return None

    @contextlib.contextmanager
    def locked(self, budget_id: str) -> Iterator[Budget]:
        """Read-modify-write one budget under a cross-process lock; changes are saved on exit."""
        path = self._path(budget_id)
        with file_lock(path.with_suffix(".lock")):
            budget = self.get(budget_id)
            if budget is None:
                raise KeyError(budget_id)
            yield budget
            budget.updated_at = _now()
            self._write(budget)

    def update(self, budget_id: str, change: Callable[[Budget], None]) -> Budget:
        with self.locked(budget_id) as b:
            change(b)
        fresh = self.get(budget_id)
        assert fresh is not None
        return fresh

    def _write(self, budget: Budget) -> None:
        write_json(self._path(budget.id), asdict(budget))
