"""PayPal Orders v2, sandbox or live, plus an in-process fake.

Flow: OAuth2 client-credentials token → create order (intent CAPTURE) → send the buyer to the
approve link → they return to `return_url?token=<order id>` → capture → status COMPLETED.

Docs: https://developer.paypal.com/docs/api/orders/v2/
"""

from __future__ import annotations

import base64
import contextlib
import json
import secrets
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx

from canonkeeper.preflight.filelock import file_lock

SANDBOX = "https://api-m.sandbox.paypal.com"
LIVE = "https://api-m.paypal.com"

# Statuses before the buyer has approved. With a `payment_source` the API answers
# PAYER_ACTION_REQUIRED rather than CREATED (observed on the sandbox, 2026-10-26).
UNAPPROVED = frozenset({"CREATED", "SAVED", "PAYER_ACTION_REQUIRED"})


@dataclass
class Order:
    id: str
    status: str
    approve_url: str | None
    amount: str
    currency: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Refund:
    id: str
    status: str  # COMPLETED | PENDING | FAILED | CANCELLED


def capture_id_of(o: Order) -> str | None:
    """The id of the order's (first) capture, which is what a refund is issued against."""
    pu = (o.raw.get("purchase_units") or [{}])[0]
    caps = (pu.get("payments") or {}).get("captures") or []
    return str(caps[0]["id"]) if caps and caps[0].get("id") else None


def capture_failed(o: Order) -> bool:
    """The capture was declined or failed: no money moved."""
    pu = (o.raw.get("purchase_units") or [{}])[0]
    caps = (pu.get("payments") or {}).get("captures") or []
    return bool(caps) and str(caps[0].get("status", "")) in ("DECLINED", "FAILED")


def capture_held(o: Order) -> bool:
    """The order was captured, but PayPal is holding the money (an eCheck clearing, a review):
    it is ours once it clears, so the job must not look unpaid or be cleaned up."""
    pu = (o.raw.get("purchase_units") or [{}])[0]
    caps = (pu.get("payments") or {}).get("captures") or []
    return bool(caps) and str(caps[0].get("status", "")) == "PENDING"


class PayPal(Protocol):
    mode: str
    client_id: str | None
    """Public client id for PayPal's JS SDK buttons; None where there are no real buttons."""

    def create_order(
        self,
        *,
        amount: str,
        currency: str,
        description: str,
        return_url: str,
        cancel_url: str,
        reference: str,
    ) -> Order: ...

    def get_order(self, order_id: str) -> Order: ...

    def capture_order(self, order_id: str) -> Order: ...

    def refund_capture(
        self, capture_id: str, amount: str | None = None, request_id: str | None = None
    ) -> Refund: ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool: ...


def _parse(data: dict[str, Any]) -> Order:
    pu = (data.get("purchase_units") or [{}])[0]
    caps = (pu.get("payments") or {}).get("captures") or []
    # Once captured, the amount that matters is the money that moved, not the order's.
    amt = (caps[0].get("amount") if caps else None) or pu.get("amount") or {}
    approve = None
    for link in data.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            approve = link.get("href")
    return Order(
        id=str(data.get("id", "")),
        status=str(data.get("status", "")),
        approve_url=approve,
        amount=str(amt.get("value", "")),
        currency=str(amt.get("currency_code", "")),
        raw=data,
    )


def capture_problem(o: Order, *, amount: str, currency: str) -> str | None:
    """Why a captured order does not pay for the job, or None if it does.

    COMPLETED on the order is not enough: the capture itself can be PENDING (an eCheck, a
    review), and nothing runs on money that has not arrived. The amount and currency are
    checked against the quote too, so an order can only ever pay for the job it was made for.
    """
    if o.status != "COMPLETED":
        return f"payment not completed: {o.status or 'unknown'}"
    pu = (o.raw.get("purchase_units") or [{}])[0]
    caps = (pu.get("payments") or {}).get("captures") or []
    if caps and str(caps[0].get("status", "COMPLETED")) != "COMPLETED":
        reason = (caps[0].get("status_details") or {}).get("reason", "")
        return (
            f"PayPal is holding the payment ({caps[0]['status']}{', ' + reason if reason else ''})"
        )
    if not o.amount or not o.currency:
        # Never assume: an unreported amount is not proof the quoted amount arrived.
        return "PayPal did not report the captured amount"
    try:
        same_amount = Decimal(o.amount) == Decimal(amount)
    except InvalidOperation:
        same_amount = False
    if not same_amount or o.currency != currency:
        return f"captured {o.amount} {o.currency}, but the quote was {amount} {currency}"
    return None


class PayPalClient:
    """Real client. `mode` is "sandbox" or "live"."""

    def __init__(
        self, client_id: str, secret: str, *, mode: str = "sandbox", timeout_s: float = 30.0
    ) -> None:
        self.mode = mode
        self.client_id: str | None = client_id  # public: PayPal's JS SDK loads with it
        self._base = SANDBOX if mode == "sandbox" else LIVE
        self._auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        self._http = httpx.Client(base_url=self._base, timeout=timeout_s)
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    def _bearer(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            r = self._http.post(
                "/v1/oauth2/token",
                headers={"Authorization": f"Basic {self._auth}"},
                data={"grant_type": "client_credentials"},
            )
            r.raise_for_status()
            data = r.json()
            self._token = str(data["access_token"])
            self._token_exp = time.time() + float(data.get("expires_in", 3600))
            return self._token

    def _headers(self, idempotency: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._bearer()}", "Content-Type": "application/json"}
        if idempotency:
            h["PayPal-Request-Id"] = idempotency
        return h

    def create_order(
        self,
        *,
        amount: str,
        currency: str,
        description: str,
        return_url: str,
        cancel_url: str,
        reference: str,
    ) -> Order:
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference,
                    "description": description[:127],
                    "custom_id": reference,
                    "amount": {"currency_code": currency, "value": amount},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Preflight by canonkeeper",
                        "user_action": "PAY_NOW",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }
        r = self._http.post("/v2/checkout/orders", json=body, headers=self._headers(reference))
        r.raise_for_status()
        o = _parse(r.json())
        if not o.amount:  # the minimal create response omits purchase_units
            o.amount, o.currency = amount, currency
        return o

    def verify_webhook(self, headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool:
        """Ask PayPal whether a webhook delivery is genuine (its signature, for this webhook).
        Anything but SUCCESS, or no answer, is False: an unverified event is ignored."""
        h = {k.lower(): v for k, v in headers.items()}
        try:
            event = json.loads(body)
            r = self._http.post(
                "/v1/notifications/verify-webhook-signature",
                json={
                    "auth_algo": h.get("paypal-auth-algo", ""),
                    "cert_url": h.get("paypal-cert-url", ""),
                    "transmission_id": h.get("paypal-transmission-id", ""),
                    "transmission_sig": h.get("paypal-transmission-sig", ""),
                    "transmission_time": h.get("paypal-transmission-time", ""),
                    "webhook_id": webhook_id,
                    "webhook_event": event,
                },
                headers=self._headers(),
            )
            r.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return False
        return str(r.json().get("verification_status", "")) == "SUCCESS"

    def get_order(self, order_id: str) -> Order:
        r = self._http.get(f"/v2/checkout/orders/{order_id}", headers=self._headers())
        r.raise_for_status()
        return _parse(r.json())

    def capture_order(self, order_id: str) -> Order:
        r = self._http.post(
            f"/v2/checkout/orders/{order_id}/capture",
            headers=self._headers(f"capture-{order_id}"),
            content=b"{}",
        )
        if r.status_code == 422 and (
            "ORDER_ALREADY_CAPTURED" in r.text or "ORDER_NOT_APPROVED" in r.text
        ):
            return self.get_order(order_id)  # callers read the status; nothing to raise
        r.raise_for_status()
        o = _parse(r.json())
        if o.status == "COMPLETED" and not o.amount:
            # A minimal capture reply can omit the amounts; the order itself always has them,
            # and capture_problem refuses to pay a job on an amount it was not shown.
            return self.get_order(order_id)
        return o

    def refund_capture(
        self, capture_id: str, amount: str | None = None, request_id: str | None = None
    ) -> Refund:
        """Refund the whole capture (an empty body means the full amount), or `amount` of it (a
        budget's unused remainder). Idempotent: the request id is fixed per capture (or given,
        one per partial refund), and an already-refunded capture is reported as done."""
        body = {"amount": {"value": amount, "currency_code": "USD"}} if amount else {}
        r = self._http.post(
            f"/v2/payments/captures/{capture_id}/refund",
            headers=self._headers(request_id or f"refund-{capture_id}"),
            content=json.dumps(body).encode(),
        )
        if r.status_code == 422 and "CAPTURE_FULLY_REFUNDED" in r.text:
            return Refund(id="", status="COMPLETED")
        r.raise_for_status()
        data = r.json()
        return Refund(id=str(data.get("id", "")), status=str(data.get("status", "")))


class FakePayPal:
    """In-process stand-in with the same state machine the sandbox showed:
    PAYER_ACTION_REQUIRED → APPROVED → COMPLETED.

    The approve URL points back at the app's own `/fake-approve` route, which flips the order
    to APPROVED and redirects to the return URL exactly as PayPal would.

    With `state_path`, orders are kept in a JSON file so two processes see the same ones: an
    agent talking to `canonkeeper mcp` creates the order, the person approves it on the page
    served by `canonkeeper serve`, and the agent's `confirm_payment` then finds it approved."""

    mode = "fake"
    client_id: str | None = None  # no JS SDK buttons for the fake: it has its own page
    WEBHOOK_SIGNATURE = "fake-signature"  # what a test sends to stand for PayPal's signature

    def verify_webhook(self, headers: Mapping[str, str], body: bytes, webhook_id: str) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        return h.get("paypal-transmission-sig") == self.WEBHOOK_SIGNATURE

    def __init__(
        self, approve_base: str = "/fake-approve", *, state_path: Path | None = None
    ) -> None:
        self._orders: dict[str, Order] = {}
        self._mem_lock = threading.RLock()
        self._approve_base = approve_base
        self._state_path = state_path
        self._load()

    def _load(self) -> None:
        if self._state_path and self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            for oid, rec in data.items():
                self._orders[oid] = Order(**rec)

    def _save(self) -> None:
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: asdict(v) for k, v in self._orders.items()}, indent=1),
            encoding="utf-8",
        )
        tmp.replace(self._state_path)

    def _get(self, order_id: str) -> Order:
        with self._lock():  # never reload underneath another thread's transaction
            self._load()  # another process may have approved it
            return self._orders[order_id]

    def _lock(self) -> contextlib.AbstractContextManager[object]:
        if self._state_path:
            return file_lock(self._state_path.with_suffix(".lock"))
        return self._mem_lock

    @contextlib.contextmanager
    def _txn(self) -> Iterator[None]:
        """Read-modify-write of the shared state file under a cross-process lock, so one
        process's change is never overwritten by another's stale copy."""
        with self._lock():
            self._load()
            yield
            self._save()

    def create_order(
        self,
        *,
        amount: str,
        currency: str,
        description: str,
        return_url: str,
        cancel_url: str,
        reference: str,
    ) -> Order:
        oid = "FAKE" + secrets.token_hex(6).upper()
        o = Order(
            id=oid,
            status="PAYER_ACTION_REQUIRED",
            approve_url=f"{self._approve_base}?token={oid}",
            amount=amount,
            currency=currency,
            # where the buyer goes after approving: fixed at creation, like PayPal's own
            # return_url, so the approval page cannot be used to redirect anywhere else
            raw={"description": description, "reference": reference, "return_url": return_url},
        )
        with self._txn():
            self._orders[oid] = o
        return o

    def approve(self, order_id: str) -> Order:
        with self._txn():
            o = self._orders[order_id]
            if o.status in UNAPPROVED:
                o.status = "APPROVED"
        return o

    def get_order(self, order_id: str) -> Order:
        return self._get(order_id)

    def capture_order(self, order_id: str) -> Order:
        with self._txn():
            o = self._orders[order_id]
            if o.status in UNAPPROVED:
                return o  # like the real client: not approved, status unchanged
            if o.status != "COMPLETED":
                o.status = "COMPLETED"
                o.raw["purchase_units"] = [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "id": "CAP" + o.id[4:],
                                    "status": "COMPLETED",
                                    "amount": {"value": o.amount, "currency_code": o.currency},
                                }
                            ]
                        }
                    }
                ]
        return o

    def refund_capture(
        self, capture_id: str, amount: str | None = None, request_id: str | None = None
    ) -> Refund:
        with self._txn():
            for o in self._orders.values():
                if capture_id_of(o) != capture_id:
                    continue
                if amount is None:
                    o.raw["refund_id"] = (
                        o.raw.get("refund_id") or "REF" + secrets.token_hex(5).upper()
                    )
                    return Refund(id=str(o.raw["refund_id"]), status="COMPLETED")
                # Partial refunds, as PayPal keeps them: one per request id, never more in
                # total than was captured.
                partial: dict[str, list[str]] = o.raw.setdefault("partial_refunds", {})
                key = request_id or secrets.token_hex(4)
                if key in partial:
                    return Refund(id=partial[key][0], status="COMPLETED")
                done = sum(Decimal(v[1]) for v in partial.values())
                if done + Decimal(amount) > Decimal(o.amount):
                    return Refund(id="", status="FAILED")
                rid = "REF" + secrets.token_hex(5).upper()
                partial[key] = [rid, amount]
                return Refund(id=rid, status="COMPLETED")
        raise KeyError(capture_id)
