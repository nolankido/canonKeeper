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
from canonkeeper.preflight.paypal import UNAPPROVED, PayPal, capture_problem
from canonkeeper.preflight.pricing import CURRENCY

PAID = frozenset({"paid", "running", "done", "failed", "deleted"})
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
            store.save(fresh)
        job.status, job.paid_at = fresh.status, fresh.paid_at
    runner.submit(job.id)
    return None
