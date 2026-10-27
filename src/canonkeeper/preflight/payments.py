"""Turning an approved PayPal order into a paid job — the one place that decides it.

The buyer approves on PayPal, and then one of three things notices: PayPal sends their browser
back to /return, an AI assistant calls confirm_payment, or the buyer (having closed the tab)
presses "Check payment" on the job page. All three come here, so the rules are the same: an
order the buyer has not approved is left alone, and a job is marked paid only when a capture
of exactly the quoted amount has completed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from canonkeeper.preflight.jobs import Job, JobStore, Runner, order_description
from canonkeeper.preflight.paypal import (
    UNAPPROVED,
    PayPal,
    capture_held,
    capture_id_of,
    capture_problem,
)
from canonkeeper.preflight.pricing import CURRENCY

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
        o = paypal.capture_order(order_id)  # idempotent: a second capture reads the first
    except httpx.HTTPStatusError as e:
        msg = _order_trouble(e, store, fresh)
        _sync(job, store.get(job.id))
        return msg
    problem = capture_problem(o, amount=fresh.quote_usd, currency=CURRENCY)

    def ours(j: Job) -> bool:  # this job's order, or one a re-order replaced
        return order_id == j.order_id or order_id in j.earlier_orders

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


def refund(job: Job, paypal: PayPal, store: JobStore) -> str | None:
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

    try:
        cap = claim.capture_id
        if not cap and claim.order_id:  # jobs paid before capture ids were recorded
            cap = capture_id_of(paypal.get_order(claim.order_id))
        if not cap:
            return give_back("PayPal has no capture for this order")
        r = paypal.refund_capture(cap)
    except httpx.HTTPStatusError as e:  # PayPal answered, and said no
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
