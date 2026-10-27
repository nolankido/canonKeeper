"""Turning an approved PayPal order into a paid job — the one place that decides it.

The buyer approves on PayPal, and then one of three things notices: PayPal sends their browser
back to /return, an AI assistant calls confirm_payment, or the buyer (having closed the tab)
presses "Check payment" on the job page. All three come here, so the rules are the same: an
order the buyer has not approved is left alone, and a job is marked paid only when a capture
of exactly the quoted amount has completed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from canonkeeper.preflight.budgets import Budget, BudgetStore
from canonkeeper.preflight.jobs import Job, JobStore, Runner, order_description
from canonkeeper.preflight.paypal import (
    UNAPPROVED,
    PayPal,
    Refund,
    capture_failed,
    capture_held,
    capture_id_of,
    capture_problem,
)
from canonkeeper.preflight.pricing import CURRENCY
from canonkeeper.preflight.quota import check_quota

PAID = frozenset({"paid", "running", "done", "failed", "refunding", "refunded", "deleted"})
OPEN = ("ordered", "cancelled", "held")  # an order exists; payment not (yet) complete
STUCK_REFUND_S = 120.0  # a "refunding" job older than this lost its process mid-call


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sync(job: Job, fresh: Job | None) -> None:
    """Callers hold `job`; make it show what is stored."""
    if fresh is not None:
        for k, v in vars(fresh).items():
            setattr(job, k, v)


def _order_trouble(e: httpx.HTTPStatusError, store: JobStore, job: Job) -> str:
    """Turn PayPal's refusals into something the buyer can act on, instead of a 500 that
    leaves the job stuck at "ordered"."""
    text = e.response.text
    if e.response.status_code == 404 or any(
        k in text for k in ("RESOURCE_NOT_FOUND", "ORDER_EXPIRED", "ORDER_ALREADY_VOIDED")
    ):
        store.update(job.id, expect=("ordered",), status="cancelled")
        return "this PayPal order has expired; start the checkout again — nothing was charged"
    if "INSTRUMENT_DECLINED" in text:
        return (
            "PayPal declined that payment method; open the PayPal link again and choose "
            "another — nothing was charged"
        )
    raise e


def settle(
    job: Job,
    paypal: PayPal,
    store: JobStore,
    runner: Runner,
    *,
    known_approved: bool = False,
    order_id_hint: str | None = None,
) -> str | None:
    """Capture `job`'s order if the buyer approved it, mark the job paid and start it.

    Returns None when the job is paid (now or before), else the reason it is not. With
    `known_approved` (PayPal just sent the buyer back) the status lookup is skipped. Decisions
    are made on the stored job and written with JobStore.update, so a concurrent /cancel,
    /return or confirm_payment cannot leave money captured on a job marked unpaid.
    """
    fresh = store.get(job.id) or job
    _sync(job, fresh)
    if fresh.status in PAID:
        return None
    if order_id_hint and order_id_hint in fresh.earlier_orders:
        fresh.order_id = order_id_hint  # PayPal came back with an order a re-order replaced
    if fresh.status not in OPEN or not fresh.order_id:
        return f"job is {fresh.status}; nothing to pay for yet"
    order_id = fresh.order_id
    try:
        if not known_approved:
            o = paypal.get_order(order_id)
            if o.status in UNAPPROVED:
                return "not approved yet"
            if o.status == "VOIDED":
                store.update(job.id, expect=("ordered",), status="cancelled")
                return "this PayPal order has expired; start the checkout again"
        claim = _claim_capture(job.id, order_id, paypal, store)
        if claim is not None:
            if claim.startswith("settle:"):
                return settle(job, paypal, store, runner, order_id_hint=claim[7:])
            return claim
        o = paypal.capture_order(order_id)  # idempotent: a second capture reads the first
    except httpx.HTTPStatusError as e:
        if e.response.status_code < 500:  # PayPal refused: nothing was captured
            store.update(job.id, where=lambda j: j.capturing == order_id, capturing=None)
        msg = _order_trouble(e, store, fresh)
        _sync(job, store.get(job.id))
        return msg
    problem = capture_problem(o, amount=fresh.quote_usd, currency=CURRENCY)

    def ours(j: Job) -> bool:  # this job's order, or one a re-order replaced
        return order_id == j.order_id or order_id in j.earlier_orders

    if problem and capture_failed(o):
        # the held payment did not clear: nothing was charged; the buyer can order again
        store.update(
            job.id, expect=OPEN, where=ours, status="cancelled", hold_reason=None, capturing=None
        )
        _sync(job, store.get(job.id))
        return "PayPal declined the payment, so nothing was charged; start the checkout again"
    if problem and capture_held(o):
        # captured, but PayPal holds the money until it clears: never "unpaid", never pruned
        store.update(
            job.id,
            expect=OPEN,
            where=ours,
            status="held",
            order_id=order_id,
            capture_id=capture_id_of(o),
            hold_reason=problem,
        )
        _sync(job, store.get(job.id))
        return problem
    if problem:
        return problem
    # A cancel can land between the capture and here (back button, second tab): the money
    # moved, so the job is paid whatever the page said.
    store.update(
        job.id,
        expect=OPEN,
        where=ours,
        status="paid",
        order_id=order_id,
        paid_at=_now(),
        capture_id=capture_id_of(o),
        hold_reason=None,
    )
    now = store.get(job.id)
    _sync(job, now)
    if now is None or now.status not in PAID:
        return f"payment {order_id} was captured but the job is now {now.status if now else 'gone'}"
    runner.submit(job.id)
    return None


CLAIM_STALE_S = 300.0  # a capture claim older than this lost its process mid-call


def _claim_capture(job_id: str, order_id: str, paypal: PayPal, store: JobStore) -> str | None:
    """Claim the job for one order before capturing it, so two approved orders of the same job
    (a replaced order approved in an old tab, the new one in another) are never both captured.

    Returns None when the claim is ours, "settle:<order>" when the order that holds the claim
    turns out to be captured already (settle that one instead), else why we must wait."""
    now = datetime.now(UTC)

    def free(j: Job) -> bool:
        return j.capturing in (None, order_id)

    if store.update(job_id, expect=OPEN, where=free, capturing=order_id, capturing_at=_now()):
        return None
    current = store.get(job_id)
    if current is None:
        return "this job no longer exists"
    if current.status in PAID:
        return "settle:" + (current.order_id or order_id)
    other = current.capturing
    if not other:
        return f"job is {current.status}; nothing to pay for yet"
    o = paypal.get_order(other)
    if o.status == "COMPLETED":
        return "settle:" + other
    since = datetime.fromisoformat(current.capturing_at or _now())
    if (now - since).total_seconds() < CLAIM_STALE_S:  # its capture may still be on the way
        return "another payment for this job is being completed; check again in a moment"

    def stale(j: Job) -> bool:
        return j.capturing == other

    if store.update(job_id, expect=OPEN, where=stale, capturing=order_id, capturing_at=_now()):
        return None
    return "another payment for this job is being completed; check again in a moment"


def refund(
    job: Job, paypal: PayPal, store: JobStore, budgets: BudgetStore | None = None
) -> str | None:
    """Give the whole payment back for an audit that failed. Returns None when the job is
    refunded (now or before), else why not.

    Only a failed job qualifies: a finished audit delivered what was paid for, and a running
    one may still finish. Refunding is the buyer's choice instead of automatic, because most
    failures (a provider's daily quota, an overloaded model) clear up on a free retry. The job
    is claimed as "refunding" first, so a retry cannot start while the refund is in flight.
    """

    def claimable(j: Job) -> bool:
        if j.status == "failed":
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(j.updated_at)
        return age.total_seconds() > STUCK_REFUND_S  # a refund whose process died

    claim = store.update(
        job.id, expect=("failed", "refunding"), where=claimable, status="refunding"
    )
    if claim is None:
        fresh = store.get(job.id)
        _sync(job, fresh)
        if fresh is not None and fresh.status == "refunded":
            return None
        if fresh is not None and fresh.status == "refunding":
            return "a refund for this job is already in progress"
        return f"only a failed audit can be refunded; this one is {job.status}"

    def give_back(reason: str) -> str:
        store.update(job.id, expect=("refunding",), status="failed")
        _sync(job, store.get(job.id))
        return reason

    if claim.budget_id and budgets is not None:
        problem = _credit_budget(claim, paypal, budgets)
        if problem:
            return give_back(problem)
        store.update(
            job.id,
            expect=("refunding",),
            status="refunded",
            refund_id=f"budget:{claim.budget_id}",
            refunded_at=_now(),
            refund_status="COMPLETED",
        )
        _sync(job, store.get(job.id))
        return None
    try:
        cap = claim.capture_id
        if not cap and claim.order_id:  # jobs paid before capture ids were recorded
            cap = capture_id_of(paypal.get_order(claim.order_id))
        if not cap:
            return give_back("PayPal has no capture for this order")
        r = paypal.refund_capture(cap)
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:  # PayPal's own trouble: we do not know; stay put
            raise
        return give_back(f"PayPal refused the refund ({e.response.status_code})")
    # Any other failure (a timeout, a dropped connection) propagates and leaves the job
    # "refunding": PayPal may already have refunded, so a retry must not start. Asking again
    # is safe (refunds are idempotent per capture), and the job page offers exactly that.
    if r.status not in ("COMPLETED", "PENDING"):
        return give_back(f"PayPal refused the refund ({r.status or 'no status'})")
    store.update(
        job.id,
        expect=("refunding",),
        status="refunded",
        capture_id=cap,
        refund_id=r.id or None,
        refunded_at=_now(),
        refund_status=r.status,
    )
    _sync(job, store.get(job.id))
    return None


class QuotaShort(Exception):
    """The day's model quota cannot finish this audit: no order was created, nothing charged."""


def place_order(
    job: Job,
    paypal: PayPal,
    store: JobStore,
    runner: Runner,
    base: str,
    *,
    absolute_links: bool = False,
) -> Job:
    """Create the PayPal order for a quoted (or cancelled) job and return the job, now
    "ordered" with its approval link. Anything else is returned unchanged.

    Before a *second* order, the first is checked: a buyer who approved it (or whose payment
    is still clearing) and then came back through "cancel" must not be charged twice, so an
    approved earlier order is settled instead of replaced.
    """
    fresh = store.get(job.id) or job
    if fresh.status not in ("quoted", "cancelled"):
        return fresh
    if fresh.order_id:
        try:
            earlier = paypal.get_order(fresh.order_id)
        except (httpx.HTTPError, KeyError):
            earlier = None  # gone or expired: nothing to settle
        if earlier is not None and earlier.status not in UNAPPROVED | {"VOIDED"}:
            settle(fresh, paypal, store, runner)
            return store.get(job.id) or fresh
    check = check_quota(
        runner.settings, store.load_series(fresh), runner.cache_dir_for(fresh), demo=fresh.demo
    )
    if not check.ok:
        raise QuotaShort(check.reason or "today's model quota cannot finish this audit")
    o = paypal.create_order(
        amount=fresh.quote_usd,
        currency=CURRENCY,
        description=order_description(fresh),
        return_url=f"{base}/return",
        cancel_url=f"{base}/cancel?job={fresh.id}",
        # the PayPal-Request-Id: a re-order must not replay the first (cancelled) order
        reference=fresh.id if not fresh.order_id else f"{fresh.id}-{fresh.order_id}",
    )
    approve = o.approve_url or ""
    if absolute_links and approve.startswith("/"):  # the fake's page, for an MCP client
        approve = f"{base}{approve}"
    placed = store.update(
        fresh.id,
        expect=("quoted", "cancelled"),
        earlier_orders=[*fresh.earlier_orders, fresh.order_id]
        if fresh.order_id
        else fresh.earlier_orders,
        order_id=o.id,
        approve_url=approve or None,
        status="ordered",
    )
    return placed or store.get(job.id) or fresh


# ---------------------------------------------------------------------------------------------
# Agent budgets (see budgets.py): funded through PayPal once, spent by the assistant.


def open_budget(
    amount: Decimal,
    paypal: PayPal,
    budgets: BudgetStore,
    base: str,
    *,
    absolute_links: bool = False,
) -> tuple[Budget, str]:
    """A budget and its PayPal order; returns (budget, spending key). Nothing can be spent until
    the person approves the order and PayPal confirms the capture."""
    budget, key = budgets.create(amount)
    o = paypal.create_order(
        amount=budget.amount_usd,
        currency=CURRENCY,
        description=f"Preflight: a ${budget.amount_usd} budget your assistant may spend on audits",
        return_url=f"{base}/budget/{budget.id}/return",
        cancel_url=f"{base}/budget/{budget.id}",
        reference=f"budget-{budget.id}",
    )
    approve = o.approve_url or ""
    if absolute_links and approve.startswith("/"):
        approve = f"{base}{approve}"

    def placed(b: Budget) -> None:
        b.order_id, b.approve_url = o.id, approve

    return budgets.update(budget.id, placed), key


def settle_budget(budget: Budget, paypal: PayPal, budgets: BudgetStore) -> str | None:
    """Fund a budget once PayPal has a completed capture of exactly its amount.

    Decisions are made on the stored budget. A budget that was cancelled is never captured, but
    if PayPal reports its order already captured (a race with the cancel), it is funded and then
    refunded in full: captured money is never kept on a cancelled budget."""
    stored = budgets.get(budget.id) or budget
    if stored.status in ("active", "closing", "closed"):
        return _finish_close_if_asked(stored, paypal, budgets)
    if stored.status not in ("ordered", "cancelled") or not stored.order_id:
        return f"this budget is {stored.status}"
    try:
        o = paypal.get_order(stored.order_id)
        if stored.status == "cancelled" and o.status != "COMPLETED":
            return "this budget was cancelled; nothing was charged"
        if o.status in UNAPPROVED:
            return "not approved yet"
        if o.status == "VOIDED":
            budgets.update(budget.id, lambda b: setattr(b, "status", "cancelled"))
            return "this PayPal order has expired"
        if o.status != "COMPLETED":
            o = paypal.capture_order(stored.order_id)
    except httpx.HTTPStatusError as e:
        return f"PayPal could not complete the payment ({e.response.status_code})"
    problem = capture_problem(o, amount=stored.amount_usd, currency=CURRENCY)
    if problem:
        return problem

    def fund(b: Budget) -> None:
        if b.status in ("ordered", "cancelled"):
            if b.status == "cancelled":
                b.close_requested = True  # the money arrived after a cancel: give it all back
            b.status, b.capture_id, b.funded_at = "active", capture_id_of(o), _now()

    funded = budgets.update(budget.id, fund)
    return _finish_close_if_asked(funded, paypal, budgets)


def _finish_close_if_asked(b: Budget, paypal: PayPal, budgets: BudgetStore) -> str | None:
    if b.close_requested and b.status in ("active", "closing"):
        return close_budget(b, paypal, budgets)
    return None


def release_unapproved_order(job: Job, paypal: PayPal, store: JobStore) -> bool:
    """Put a job whose PayPal order the person never approved back to "quoted", so it can be
    paid another way (an assistant's budget). True if released.

    Safe against paying twice: the released order is kept in `earlier_orders`, and `settle`
    returns before capturing anything for a job that is already paid, so if the person approves
    that order later, PayPal never captures it and it expires. An order that is approved,
    being captured, or held is never released."""
    fresh = store.get(job.id) or job
    order_id = fresh.order_id
    if fresh.status not in ("ordered", "cancelled") or not order_id or fresh.capturing:
        return False
    try:
        o = paypal.get_order(order_id)
    except (httpx.HTTPError, KeyError):
        return False
    if o.status not in UNAPPROVED | {"VOIDED"}:
        return False
    earlier = [*fresh.earlier_orders, order_id]
    released = store.update(
        job.id,
        expect=("ordered", "cancelled"),
        where=lambda j: j.order_id == order_id and not j.capturing,
        status="quoted",
        order_id=None,
        approve_url=None,
        earlier_orders=earlier,
    )
    _sync(job, store.get(job.id))
    return released is not None


def spend_budget(
    key: str, job: Job, store: JobStore, budgets: BudgetStore, runner: Runner
) -> str | None:
    """Pay a quoted job from a budget and start it. Never past the balance, never a job that
    has (or had) a PayPal order of its own: one payment per job, whichever way it came."""
    budget = budgets.by_key(key)
    if budget is None:
        return "unknown budget key"
    fresh = store.get(job.id) or job
    if fresh.status != "quoted" or fresh.order_id:
        return f"this job is {fresh.status}{' with a PayPal order of its own' if fresh.order_id else ''}; only a quoted job can be paid from a budget"
    check = check_quota(
        runner.settings, store.load_series(fresh), runner.cache_dir_for(fresh), demo=fresh.demo
    )
    if not check.ok:
        raise QuotaShort(check.reason or "today's model quota cannot finish this audit")
    price = Decimal(fresh.quote_usd)
    with budgets.locked(budget.id) as b:
        if b.status != "active":
            return f"this budget is {b.status}"
        if b.balance < price:
            return f"the budget has ${b.balance:.2f} left; this costs ${price:.2f}"
        paid = store.update(
            fresh.id, expect=("quoted",), status="paid", paid_at=_now(), budget_id=b.id
        )
        if paid is None:
            return "this job is no longer waiting to be paid"
        b.spends.append(
            {"job_id": fresh.id, "amount": f"{price:.2f}", "at": _now(), "kind": fresh.kind}
        )
    runner.submit(fresh.id)
    _sync(job, store.get(job.id))
    return None


def close_budget(budget: Budget, paypal: PayPal, budgets: BudgetStore) -> str | None:
    """Stop spending and refund the unused balance to the person's PayPal account.

    No lock is held across a PayPal call: the refund is first recorded as pending (amount and
    request id fixed, the budget no longer spendable), then sent, then its answer recorded. A
    retry after a timeout sends the same refund again, which PayPal answers once."""
    stored = budgets.get(budget.id)
    if stored is None:
        return "unknown budget"
    if stored.status == "ordered":
        # Never cancel on a stale copy, and never cancel money PayPal already holds: ask PayPal.
        try:
            o = paypal.get_order(stored.order_id or "")
        except (httpx.HTTPError, KeyError):
            o = None
        if o is None or o.status in UNAPPROVED | {"VOIDED"}:

            def cancel(b: Budget) -> None:
                if b.status == "ordered":
                    b.status = "cancelled"

            after = budgets.update(budget.id, cancel)
            if after.status == "cancelled":
                return None
            stored = after
        else:
            budgets.update(budget.id, lambda b: setattr(b, "close_requested", True))
            problem = settle_budget(stored, paypal, budgets)
            after = budgets.get(budget.id) or stored
            if after.status == "closed":
                return None
            return (
                f"{problem}; the budget will be refunded in full as soon as PayPal confirms it"
                if problem
                else None
            )
    if stored.status == "cancelled":
        return None

    def claim(b: Budget) -> None:
        if b.status in ("active", "closing", "closed"):
            b.close_requested = True
            if b.status == "active":
                b.status = "closing"
            left = b.balance
            if left > 0:  # includes a closed budget that a late credit put money back into
                n = sum(1 for r in b.refunds if r.get("for") == "unused balance")
                b.refunds.append(
                    {
                        "request_id": f"budget-{b.id}-close-{n}",
                        "amount": f"{left:.2f}",
                        "at": _now(),
                        "status": "SENDING",
                        "for": "unused balance",
                    }
                )

    budgets.update(budget.id, claim)
    problem = _send_pending_refunds(budget.id, paypal, budgets)

    def done(b: Budget) -> None:
        if b.status == "closing" and not any(r["status"] == "SENDING" for r in b.refunds):
            b.status = "closed"

    budgets.update(budget.id, done)
    return problem


def _send_pending_refunds(budget_id: str, paypal: PayPal, budgets: BudgetStore) -> str | None:
    """Send every refund recorded as SENDING, outside the lock, and record each answer. A refused
    refund is taken off the ledger (the money stays in the budget) and reported; a timeout or a
    5xx leaves it SENDING, to be sent again with the same request id."""
    b = budgets.get(budget_id)
    if b is None:
        return "unknown budget"
    problems: list[str] = []
    for r in [r for r in b.refunds if r["status"] == "SENDING"]:
        if not b.capture_id:
            return "PayPal has no capture for this budget"
        try:
            answer = paypal.refund_capture(b.capture_id, r["amount"], r["request_id"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise
            answer = None
            problems.append(f"PayPal refused the refund ({e.response.status_code})")
        if answer is not None and answer.status not in ("COMPLETED", "PENDING"):
            problems.append(f"PayPal refused the refund ({answer.status or 'no status'})")
            answer = None

        def record(bb: Budget, rid: str = r["request_id"], a: Refund | None = answer) -> None:
            for entry in bb.refunds:
                if entry.get("request_id") == rid and entry["status"] == "SENDING":
                    if a is None:
                        bb.refunds.remove(entry)
                        if bb.status == "closing":
                            bb.status = "active"
                            bb.close_requested = False
                    else:
                        entry["status"], entry["refund_id"] = a.status, a.id
                    return

        budgets.update(budget_id, record)
    return "; ".join(problems) or None


def _credit_budget(job: Job, paypal: PayPal, budgets: BudgetStore) -> str | None:
    """A failed audit paid from a budget: the money goes back to the budget, or, once the budget
    is closing or closed, straight back to PayPal."""
    assert job.budget_id
    return _give_back_to_budget(
        job.budget_id, job.id, job.id, Decimal(job.quote_usd), "audit failed", paypal, budgets
    )


def _give_back_to_budget(
    budget_id: str,
    key: str,
    job_id: str,
    amount: Decimal,
    reason: str,
    paypal: PayPal,
    budgets: BudgetStore,
) -> str | None:
    """Credit `amount` to a budget once per `key`. Once the budget is closing or closed, the
    credit is matched by a refund to PayPal (recorded as pending first, sent outside the lock)."""
    if budgets.get(budget_id) is None:
        return "the budget this job was paid from no longer exists"

    def credit(b: Budget) -> None:
        if any(c.get("key", c["job_id"]) == key for c in b.credits):
            return  # already given back (a pending refund, if any, is sent below)
        refund_too = b.status in ("closing", "closed")
        b.credits.append(
            {
                "job_id": job_id,
                "key": key,
                "amount": f"{amount:.2f}",
                "at": _now(),
                "reason": f"{reason}; refunded to PayPal" if refund_too else reason,
            }
        )
        if refund_too:
            b.refunds.append(
                {
                    "request_id": f"budget-{b.id}-{key}",
                    "amount": f"{amount:.2f}",
                    "at": _now(),
                    "status": "SENDING",
                    "for": reason,
                }
            )

    budgets.update(budget_id, credit)
    return _send_pending_refunds(budget_id, paypal, budgets)


# ---------------------------------------------------------------------------------------------
# The finding guarantee (decision 1): a contradiction the author marks wrong earns money back.

GUARANTEE_USD = Decimal("0.50")
GUARANTEE_CAP = Decimal("0.5")  # at most half the price, however many findings are marked


def mark_finding_wrong(
    job: Job,
    index: int,
    note: str,
    paypal: PayPal,
    store: JobStore,
    budgets: BudgetStore | None,
) -> tuple[str | None, Decimal]:
    """Record the author's "this contradiction is wrong" and give back GUARANTEE_USD through
    PayPal (or to the budget that paid), while the guarantee stays under half the price. Returns
    (problem, amount given back). The mark shows on the report beside the model's call.

    The amount is decided once, under the job's lock, and recorded as pending before PayPal is
    asked; pending amounts count toward the cap. A retry after a timeout sends the same refund
    (same amount, same request id) instead of deciding again. No lock is held across PayPal."""
    from canonkeeper.models import AuditReport

    reports = store.dir(job.id) / "reports"
    with store.lock(job.id):
        fresh = store.get(job.id)
        if fresh is None or fresh.status != "done":
            return "only a finished audit's findings can be marked", Decimal(0)
        report = AuditReport.model_validate_json(
            (reports / "report.json").read_text(encoding="utf-8")
        )
        if (
            not 0 <= index < len(report.findings)
            or report.findings[index].verdict != "contradiction"
        ):
            return "no such contradiction in this report", Decimal(0)
        entry = next((g for g in fresh.guarantee if g["finding"] == str(index)), None)
        if entry is not None and entry.get("status", "done") == "done":
            return None, Decimal(0)  # marked before; nothing more to give
        if entry is None:
            given = sum((Decimal(g["amount"]) for g in fresh.guarantee), Decimal(0))
            cap = (Decimal(fresh.quote_usd) * GUARANTEE_CAP).quantize(Decimal("0.01"))
            amount = GUARANTEE_USD if given + GUARANTEE_USD <= cap else Decimal(0)
            entry = {
                "finding": str(index),
                "amount": f"{amount:.2f}",
                "refund_id": "",
                "request_id": f"guarantee-{job.id}-{index}",
                "at": _now(),
                "note": note[:500],
                "headline": _headline(report, index),
                "status": "pending" if amount else "done",
            }
            store.update(job.id, guarantee=[*fresh.guarantee, entry])
    amount = Decimal(entry["amount"])
    refund_id = ""
    if amount and fresh.budget_id and budgets is not None:
        problem = _give_back_to_budget(
            fresh.budget_id, f"{job.id}-finding-{index}", job.id, amount,
            f"marked wrong: {entry.get('headline') or 'a finding'}", paypal, budgets,
        )  # fmt: skip
        if problem:
            _drop_guarantee(store, job.id, index)
            return problem, Decimal(0)
        refund_id = f"budget:{fresh.budget_id}"
    elif amount:
        if not fresh.capture_id:
            _drop_guarantee(store, job.id, index)
            return "PayPal has no capture for this job", Decimal(0)
        try:
            r = paypal.refund_capture(
                fresh.capture_id,
                entry["amount"],
                entry.get("request_id", f"guarantee-{job.id}-{index}"),
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise  # PayPal may have refunded: the entry stays pending, a retry resends it
            _drop_guarantee(store, job.id, index)
            return f"PayPal refused the refund ({e.response.status_code})", Decimal(0)
        if r.status not in ("COMPLETED", "PENDING"):
            _drop_guarantee(store, job.id, index)
            return f"PayPal refused the refund ({r.status or 'no status'})", Decimal(0)
        refund_id = r.id

    def done(g: dict[str, str]) -> dict[str, str]:
        if g["finding"] == str(index):
            return {**g, "status": "done", "refund_id": refund_id or g.get("refund_id", "")}
        return g

    with store.lock(job.id):
        now = store.get(job.id)
        if now is not None:
            store.update(job.id, guarantee=[done(g) for g in now.guarantee])
            from canonkeeper.preflight.rulings import annotate_report

            annotate_report(store.get(job.id) or now, store)
    _sync(job, store.get(job.id))
    return None, amount


def _headline(report: object, index: int) -> str:
    from canonkeeper.report.display import headline

    try:
        return headline(report.findings[index].candidate)  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return ""


def _drop_guarantee(store: JobStore, job_id: str, index: int) -> None:
    """PayPal refused: nothing was given back, so the mark is taken off to be tried again."""
    with store.lock(job_id):
        now = store.get(job_id)
        if now is not None:
            store.update(job_id, guarantee=[g for g in now.guarantee if g["finding"] != str(index)])
