"""T9: the finding guarantee. A contradiction the author marks wrong is noted on the report, and
$0.50 of the price goes back through PayPal (or to the budget that paid), at most half the price."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
from canonkeeper.preflight.payments import GUARANTEE_USD
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


@pytest.fixture
def shop(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    return TestClient(app), app.state.mcp_service


def _paid_audit(svc) -> str:
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    order = svc.order_audit(job)
    svc.paypal.approve(order["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    assert svc.store.get(job).status == "done"
    return job


def _partials(svc, job) -> dict:
    order = svc.paypal.get_order(svc.store.get(job).order_id)
    return order.raw.get("partial_refunds", {})


def test_marking_a_contradiction_wrong_gives_money_back_and_notes_it_on_the_report(shop):
    web, svc = shop
    job = _paid_audit(svc)
    findings = svc.get_findings(job)["findings"]
    first = findings[0]["index"]
    page = web.get(f"/jobs/{job}").text
    assert 'data-testid="guarantee"' in page and f'data-testid="mark-wrong-{first}"' in page

    r = web.post(
        f"/jobs/{job}/findings/{first}/wrong", data={"note": "a disguise"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert [v[1] for v in _partials(svc, job).values()] == ["0.50"]  # back through PayPal
    page = web.get(f"/jobs/{job}").text
    assert "marked wrong" in page and "$0.50 back" in page
    report = web.get(f"/jobs/{job}/report").text
    assert "The author marked this call wrong." in report and "a disguise" in report

    web.post(f"/jobs/{job}/findings/{first}/wrong", data={"note": "again"})  # once only
    assert len(_partials(svc, job)) == 1


def test_the_guarantee_stops_at_half_the_price(shop):
    """$5.05 audit: half is $2.52, so five findings earn $0.50 each ($2.50) and a sixth only a note."""
    _web, svc = shop
    job = _paid_audit(svc)
    n = len(svc.get_findings(job)["findings"])
    report = svc.store.dir(job) / "reports" / "report.json"
    import json

    data = json.loads(report.read_text(encoding="utf-8"))
    base = next(f for f in data["findings"] if f["verdict"] == "contradiction")
    data["findings"] += [base] * (7 - n)  # enough contradictions to reach the cap
    report.write_text(json.dumps(data), encoding="utf-8")
    contradictions = [i for i, f in enumerate(data["findings"]) if f["verdict"] == "contradiction"]
    given = [svc.mark_finding_wrong(job, i)["given_back"] for i in contradictions[:7]]
    assert given[:5] == ["0.50 USD"] * 5 and set(given[5:]) == {"0.00 USD"}
    total = sum(Decimal(v[1]) for v in _partials(svc, job).values())
    assert total == 5 * GUARANTEE_USD <= Decimal("5.05") / 2


def test_only_a_finished_audits_contradictions_can_be_marked(shop):
    web, svc = shop
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    assert (
        svc.mark_finding_wrong(job, 0)["reason"] == "only a finished audit's findings can be marked"
    )
    done = _paid_audit(svc)
    assert svc.mark_finding_wrong(done, 999)["reason"] == "no such contradiction in this report"
    cleared = next(
        (
            f["index"]
            for f in svc.get_findings(done, include_cleared=True)["findings"]
            if f["verdict"] != "contradiction"
        ),
        None,
    )
    if cleared is not None:
        assert svc.mark_finding_wrong(done, cleared)["marked"] is False
    assert web.post(f"/jobs/{done}/findings/999/wrong", data={}).status_code == 409


def test_a_budget_paid_audit_gets_its_guarantee_back_into_the_budget(shop):
    _web, svc = shop
    b = svc.open_budget("20")
    svc.paypal.approve(svc.budgets.get(b["budget_id"]).order_id)
    svc.confirm_budget(b["budget_id"])
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.pay_with_budget(job, b["budget_key"])
    svc.runner.wait(job, timeout=60)
    first = svc.get_findings(job)["findings"][0]["index"]
    assert svc.mark_finding_wrong(job, first, "maiden name")["given_back"] == "0.50 USD"
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "15.45"  # 20 - 5.05 + 0.50
    svc.mark_finding_wrong(job, first)  # again: nothing more
    assert svc.budget_status(b["budget_key"])["balance_usd"] == "15.45"
