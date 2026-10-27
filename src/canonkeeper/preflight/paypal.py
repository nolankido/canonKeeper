"""PayPal Orders v2, sandbox or live, plus an in-process fake.

Flow: OAuth2 client-credentials token → create order (intent CAPTURE) → send the buyer to the
approve link → they return to `return_url?token=<order id>` → capture → status COMPLETED.

Docs: https://developer.paypal.com/docs/api/orders/v2/
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

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


class PayPal(Protocol):
    mode: str

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


def _parse(data: dict[str, Any]) -> Order:
    pu = (data.get("purchase_units") or [{}])[0]
    amt = pu.get("amount") or {}
    if not amt and pu.get("payments"):
        caps = pu["payments"].get("captures") or [{}]
        amt = caps[0].get("amount", {})
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


class PayPalClient:
    """Real client. `mode` is "sandbox" or "live"."""

    def __init__(
        self, client_id: str, secret: str, *, mode: str = "sandbox", timeout_s: float = 30.0
    ) -> None:
        self.mode = mode
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
        return _parse(r.json())


class FakePayPal:
    """In-process stand-in with the same state machine the sandbox showed:
    PAYER_ACTION_REQUIRED → APPROVED → COMPLETED.

    The approve URL points back at the app's own `/fake-approve` route, which flips the order
    to APPROVED and redirects to the return URL exactly as PayPal would."""

    mode = "fake"

    def __init__(self, approve_base: str = "/fake-approve") -> None:
        self._orders: dict[str, Order] = {}
        self._approve_base = approve_base

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
            approve_url=f"{self._approve_base}?token={oid}&return_to={return_url}",
            amount=amount,
            currency=currency,
            raw={"description": description, "reference": reference},
        )
        self._orders[oid] = o
        return o

    def approve(self, order_id: str) -> Order:
        o = self._orders[order_id]
        if o.status in UNAPPROVED:
            o.status = "APPROVED"
        return o

    def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    def capture_order(self, order_id: str) -> Order:
        o = self._orders[order_id]
        if o.status in UNAPPROVED:
            return o  # like the real client: not approved, status unchanged
        o.status = "COMPLETED"
        return o
