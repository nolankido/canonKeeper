"""Review: a signature-verified webhook whose body is JSON but not an event object 500s."""

from __future__ import annotations

from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.paypal import FakePayPal


def test_a_webhook_body_that_is_not_an_object_is_a_400_not_a_500(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    web = TestClient(app, raise_server_exceptions=False)
    signed = {"paypal-transmission-sig": FakePayPal.WEBHOOK_SIGNATURE}
    for body in ([], "x", {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": "x"}):
        r = web.post("/paypal/webhook", json=body, headers=signed)
        assert r.status_code < 500, (body, r.status_code)
