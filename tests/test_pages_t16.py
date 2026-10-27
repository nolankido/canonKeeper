"""T16: what a person sees — plain status words, the price arithmetic wherever a price is asked,
PayPal's colours on the pay steps, and no balance shown for a budget nobody has funded yet."""

from __future__ import annotations

from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import STATUS_LABELS, create_app
from canonkeeper.preflight.paypal import FakePayPal

BOOKS = [
    {"name": "01-the-harbour.txt", "text": BOOK1},
    {"name": "02-the-wreck.txt", "text": BOOK2},
    {"name": "03-the-return.txt", "text": BOOK3},
]


def _shop(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf")
    return TestClient(app), app.state.mcp_service


def test_a_quoted_job_shows_its_arithmetic_and_a_paypal_button_in_plain_words(tmp_path):
    web, svc = _shop(tmp_path)
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    page = web.get(f"/jobs/{job}").text
    assert ">quote ready</span>" in page and ">quoted</span>" not in page
    assert "$5.00 base + 1,171 tokens at $4.00 per 100k tokens ($0.05) = $5.05" in page
    assert 'class="paypal">Pay $5.05 with PayPal' in page
    assert "Download the report first" not in page  # there is no report yet
    assert 'class="paypal"' in web.post("/quote-demo").text


def test_an_unfunded_budget_shows_no_balance_and_a_closing_one_says_so(tmp_path):
    web, svc = _shop(tmp_path)
    b = svc.open_budget("20")
    page = web.get(f"/budget/{b['budget_id']}").text
    assert "waiting for your approval on PayPal" in page and "budget-balance" not in page
    assert 'class="btn paypal"' in page and ">waiting for PayPal</span>" in page
    svc.budgets.update(b["budget_id"], lambda x: setattr(x, "status", "closing"))
    assert "on its way back to your PayPal account" in web.get(f"/budget/{b['budget_id']}").text


def test_every_status_a_job_or_budget_can_have_has_words():
    from canonkeeper.preflight.budgets import Budget

    job_statuses = {"quoted", "ordered", "cancelled", "held", "paid", "running", "done",
                    "failed", "refunding", "refunded", "deleted"}  # fmt: skip
    budget_statuses = set(Budget.__dataclass_fields__["status"].default.split()) | {
        "ordered", "active", "closing", "closed", "cancelled"
    }  # fmt: skip
    assert job_statuses | budget_statuses <= set(STATUS_LABELS)


def test_a_customers_report_credits_the_models_plainly_and_shows_no_cost_tile(tmp_path):
    from canonkeeper.models import AuditReport
    from canonkeeper.report.render import write_report
    from tests.test_report_display import _report

    r: AuditReport = _report(cost_usd=1.55, read_cost_usd=1.40, judge_cost_usd=0.15)
    r.models = {"extract": "gemini-3.5-flash-lite", "adjudicate": "gemini-3.6-flash",
                "provider": "gemini", "adjudicated_by": "gemini-3.6-flash ×12, gemini-3.5-flash-lite ×3"}  # fmt: skip
    html = write_report(r, tmp_path / "c", customer=True, back_url="/jobs/x")[0].read_text()
    assert "list-price" not in html and "$1.55" not in html and "Models:" not in html
    assert (
        "Facts read by gemini-3.5-flash-lite; conflicts judged by gemini-3.6-flash and "
        "gemini-3.5-flash-lite." in html
    )
    assert (
        'href="/jobs/x" data-testid="back-to-job"' in html and "possible conflicts checked" in html
    )
    cli = write_report(r, tmp_path / "d")[0].read_text()
    assert "read $1.40 · judge $0.15" in cli and "Models:" in cli  # the CLI keeps both


def test_bible_pages_say_yes_not_true_and_link_back_to_their_job(tmp_path):
    from canonkeeper.report.display import value

    class C:
        object, predicate = "true", "dead"

    assert value(C()) == "yes"  # type: ignore[arg-type]
    web, svc = _shop(tmp_path)
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    for page in ("bible", "bible-grid.html"):
        html = web.get(f"/jobs/{job}/{page}").text
        assert f'href="/jobs/{job}" data-testid="back-to-job"' in html, page
        assert "where the books disagree" in html and "slots" not in html.split("<main>")[1][:600]
    assert "hide: narrow" in web.get(f"/jobs/{job}/bible-grid.html").text  # phone columns
