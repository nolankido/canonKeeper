"""A deployment registers its own PayPal webhook through the REST API, so nobody has to add one
in PayPal's dashboard and copy its id into the host's settings."""

from __future__ import annotations

import json
import time

import httpx
import respx
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.preflight.app import WEBHOOK_EVENTS, WebhookId, create_app
from canonkeeper.preflight.paypal import SANDBOX, FakePayPal, PayPalClient

URL = "https://preflight-x.onrender.com/paypal/webhook"


def _token() -> None:
    respx.post(f"{SANDBOX}/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )


@respx.mock
def test_an_existing_webhook_for_this_url_is_reused_and_given_missing_events():
    _token()
    respx.get(f"{SANDBOX}/v1/notifications/webhooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "webhooks": [
                    {"id": "OTHER", "url": "https://elsewhere.example/hook", "event_types": []},
                    {"id": "WH1", "url": URL, "event_types": [{"name": "CHECKOUT.ORDER.APPROVED"}]},
                ]
            },
        )  # fmt: skip
    )
    patch = respx.patch(f"{SANDBOX}/v1/notifications/webhooks/WH1").mock(
        return_value=httpx.Response(200, json={"id": "WH1"})
    )
    create = respx.post(f"{SANDBOX}/v1/notifications/webhooks")
    pp = PayPalClient("cid", "sec", mode="sandbox")
    assert pp.ensure_webhook(URL, WEBHOOK_EVENTS) == "WH1"
    assert not create.called
    sent = json.loads(patch.calls.last.request.content)
    assert {e["name"] for e in sent[0]["value"]} == set(WEBHOOK_EVENTS)


@respx.mock
def test_a_missing_webhook_is_created_with_both_events():
    _token()
    respx.get(f"{SANDBOX}/v1/notifications/webhooks").mock(
        return_value=httpx.Response(200, json={"webhooks": []})
    )
    create = respx.post(f"{SANDBOX}/v1/notifications/webhooks").mock(
        return_value=httpx.Response(201, json={"id": "WH2"})
    )
    assert PayPalClient("cid", "sec").ensure_webhook(URL, WEBHOOK_EVENTS) == "WH2"
    body = json.loads(create.calls.last.request.content)
    assert body["url"] == URL and [e["name"] for e in body["event_types"]] == list(WEBHOOK_EVENTS)


class SandboxLike(FakePayPal):
    mode = "sandbox"
    asked: list[str] = []

    def ensure_webhook(self, url, events):  # noqa: ANN001, ANN201
        self.asked.append(url)
        return "WH-SELF"

    def verify_webhook(self, headers, body, webhook_id):  # noqa: ANN001, ANN201
        return webhook_id == "WH-SELF" and super().verify_webhook(headers, body, webhook_id)


def test_a_public_deployment_registers_its_webhook_and_verifies_against_it(tmp_path, monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    pp = SandboxLike()
    app = create_app(
        settings=Settings(provider="mock", workdir=tmp_path / "w", _env_file=None),
        paypal=pp,
        workdir=tmp_path / "pf",
        public_base_url="https://preflight-x.onrender.com",
    )
    web = TestClient(app)
    for _ in range(100):
        if web.get("/healthz").json()["webhook"] == "ready":
            break
        time.sleep(0.02)
    assert pp.asked == [URL]
    signed = {"paypal-transmission-sig": FakePayPal.WEBHOOK_SIGNATURE}
    event = {"event_type": "SOMETHING.ELSE", "resource": {}}
    assert web.post("/paypal/webhook", json=event, headers=signed).json() == {
        "ignored": "SOMETHING.ELSE"
    }


def test_a_local_run_does_not_try_to_register():
    hook = WebhookId("")
    pp = SandboxLike()
    pp.asked = []
    hook.register_in_background(pp, "http://127.0.0.1:8787")
    assert pp.asked == [] and "public https" in hook.note
    assert WebhookId("WH-SET").value == "WH-SET"  # the variable still wins when set
