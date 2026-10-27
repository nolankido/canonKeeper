"""Turning an approved PayPal order into a paid job — the one place that decides it.

The buyer approves on PayPal, and then one of three things notices: PayPal sends their browser
back to /return, an AI assistant calls confirm_payment, or the buyer (having closed the tab)
presses "Check payment" on the job page. All three come here, so the rules are the same: an
order the buyer has not approved is left alone, and a job is marked paid only when a capture
of exactly the quoted amount has completed.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from canonkeeper.preflight.jobs import Job, JobStore, Runner
from canonkeeper.preflight.paypal import UNAPPROVED, PayPal, capture_id_of, capture_problem
from canonkeeper.preflight.pricing import CURRENCY

PAID = frozenset({"paid", "running", "done", "failed", "refunded", "deleted"})
_lock = threading.Lock()


def settle(
    job: Job, paypal: PayPal, store: JobStore, runner: Runner, *, known_approved: bool = False
) -> str | None:
    """Capture `job`'s order if the buyer approved it, mark the job paid and start it.

    Returns None when the job is paid (now or before), else the reason it is not. With
    `known_approved` (PayPal just sent the buyer back) the status lookup is skipped.
    """
    if job.status in PAID:
        return None
    if job.status != "ordered" or not job.order_id:
        return f"job is {job.status}; nothing to pay for yet"
    if not known_approved:
        o = paypal.get_order(job.order_id)
        if o.status in UNAPPROVED:
            return "not approved yet"
    o = paypal.capture_order(job.order_id)  # idempotent: a second capture reads the first
    problem = capture_problem(o, amount=job.quote_usd, currency=CURRENCY)
    if problem:
        return problem
    with _lock:  # /return and confirm_payment can race; only one marks it paid
        fresh = store.get(job.id) or job
        if fresh.status == "ordered":
            fresh.status, fresh.paid_at = "paid", datetime.now(UTC).isoformat()
            fresh.capture_id = capture_id_of(o)
            store.save(fresh)
        job.status, job.paid_at = fresh.status, fresh.paid_at
    runner.submit(job.id)
    return None


def refund(job: Job, paypal: PayPal, store: JobStore) -> str | None:
    """Give the whole payment back for an audit that failed. Returns None when the job is
    refunded (now or before), else why not.

    Only a failed job qualifies: a finished audit delivered what was paid for, and a running
    one may still finish. Refunding is the buyer's choice instead of automatic, because most
    failures (a provider's daily quota, an overloaded model) clear up on a free retry.
    """
    if job.status == "refunded":
        return None
    if job.status != "failed":
        return f"only a failed audit can be refunded; this one is {job.status}"
    cap = job.capture_id
    if not cap and job.order_id:  # jobs paid before capture ids were recorded
        cap = capture_id_of(paypal.get_order(job.order_id))
    if not cap:
        return "PayPal has no capture for this order"
    r = paypal.refund_capture(cap)
    if r.status not in ("COMPLETED", "PENDING"):
        return f"PayPal refused the refund ({r.status or 'no status'})"
    with _lock:
        fresh = store.get(job.id) or job
        fresh.status, fresh.capture_id = "refunded", cap
        fresh.refund_id, fresh.refunded_at = r.id or None, datetime.now(UTC).isoformat()
        store.save(fresh)
        job.status, job.refund_id, job.refunded_at = (
            fresh.status,
            fresh.refund_id,
            fresh.refunded_at,
        )
    return None
