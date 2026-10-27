"""Smoke-test a deployed Preflight (e.g. the Render instance) without paying or running a model.

    python scripts/smoke_hosted.py https://preflight-xxxx.onrender.com
    python scripts/smoke_hosted.py https://preflight-xxxx.onrender.com --order

Checks: /healthz (model key present, PayPal mode), the home page, the sample report, and a
quote for the built-in demo series. With --order it also creates a PayPal order for that quote
and checks the redirect goes to PayPal's approval page (sandbox orders cost nothing and expire
unapproved); nothing is ever approved or captured, so no model runs. The quoted job is deleted
afterwards. Standard library only, so it runs anywhere Python does. Exit code 0 = all passed.

Render's free plan sleeps when idle; the first request can take a minute while it wakes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_open = urllib.request.build_opener(_NoRedirect).open


def fetch(
    url: str, data: dict[str, str] | None = None, timeout: float = 30
) -> tuple[int, dict[str, str], str]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    try:
        with _open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="the deployed URL, e.g. https://preflight-xxxx.onrender.com")
    ap.add_argument("--order", action="store_true", help="also create an (unapproved) order")
    ap.add_argument("--wake-timeout", type=float, default=120, help="seconds for the first call")
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> bool:
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
        return ok

    try:
        status, _, text = fetch(f"{base}/healthz", timeout=a.wake_timeout)
    except OSError as e:
        check("reachable", False, str(e))
        return 1
    health = json.loads(text) if status == 200 else {}
    check("healthz", status == 200, text[:120])
    check("model key set", health.get("model") == "ready", f"model={health.get('model')}")
    mode = health.get("paypal", "?")
    check("PayPal configured", mode in ("sandbox", "live"), f"paypal={mode}")

    status, _, text = fetch(f"{base}/")
    check("home page", status == 200 and "Preflight" in text, f"HTTP {status}")
    status, _, text = fetch(f"{base}/sample")
    check("sample report", status == 200 and "<html" in text.lower(), f"HTTP {status}")

    status, _, text = fetch(f"{base}/quote-demo", data={})
    m = re.search(r'action="/jobs/([A-Za-z0-9_-]+)/order"', text)
    price = re.search(r"Pay \$([\d.,]+)", text)
    if not check(
        "demo quote",
        status == 200 and m is not None,
        f"HTTP {status}" + (f", ${price.group(1)}" if price else ""),
    ):
        return failures
    assert m is not None
    job = m.group(1)

    if a.order:
        status, headers, _ = fetch(f"{base}/jobs/{job}/order", data={})
        loc = headers.get("Location") or headers.get("location") or ""
        host = urllib.parse.urlparse(loc).hostname or ""
        if mode == "fake":
            to_paypal = loc.startswith(("/fake-approve", f"{base}/fake-approve"))
        else:
            sandbox = host == "sandbox.paypal.com" or host.endswith(".sandbox.paypal.com")
            live = (host == "paypal.com" or host.endswith(".paypal.com")) and not sandbox
            to_paypal = sandbox if mode == "sandbox" else live
        check("order → PayPal approval", status == 303 and to_paypal, loc[:100])
        status, _, text = fetch(f"{base}/jobs/{job}")
        check("job page: waiting for approval", status == 200 and "Waiting for PayPal" in text)
        # an ordered job is kept until its order expires (purge refuses while a payment may be
        # in flight); the server's unpaid-job pruning removes it
        print(f"     job {job} left to expire with its unapproved order")
    else:
        status, _, _ = fetch(f"{base}/jobs/{job}/delete", data={"confirm": "yes"})
        check("clean up quoted job", status == 303, f"HTTP {status}")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
