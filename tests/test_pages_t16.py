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
    tokens = svc.store.get(job).tokens
    assert f"$5.00 base + {tokens:,} tokens at $4.00 per 100k tokens ($0.05) = $5.05" in page
    assert 'class="paypal">Pay $5.05 with PayPal' in page
    assert "Download the report first" not in page  # there is no report yet
    assert 'class="paypal"' in web.post("/quote-demo", follow_redirects=True).text


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
    html = write_report(r, tmp_path / "c", customer=True, back_url="/jobs/x")[0].read_text(
        encoding="utf-8"
    )
    assert "list-price" not in html and "$1.55" not in html and "Models:" not in html
    assert (
        "Facts read by gemini-3.5-flash-lite; conflicts judged by gemini-3.6-flash and "
        "gemini-3.5-flash-lite." in html
    )
    assert (
        'href="/jobs/x" data-testid="back-to-job"' in html
        and "possible conflicts, each judged" in html
    )
    cli = write_report(r, tmp_path / "d")[0].read_text(encoding="utf-8")
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


def test_the_explorer_shows_each_fact_by_book_with_its_passages():
    """AG Grid view 2: one row per (entity, fact), one column per book in reading order."""
    from canonkeeper.report.bible import bible_matrix

    class P:
        def __init__(self, book, i, ch="Ch 1"):
            self.book, self.book_title, self.book_index, self.chapter = book, book, i, ch
            self.quote = f"quote in {book}"

    class C:
        def __init__(self, obj, book, i):
            self.object, self.predicate, self.provenance = obj, "wound_location", P(book, i)

    from canonkeeper.report.bible import EntityView, SlotView

    slot = SlotView("wound_location", [("shoulder", [C("shoulder", "Scarlet", 0)]),
                                       ("leg", [C("leg", "Sign", 1)])])  # fmt: skip
    view = EntityView(name="John Watson", aliases=[], n_claims=2, first_seen="", slots=[slot])
    books, rows = bible_matrix([view])
    assert books == ["Scarlet", "Sign"]
    assert rows == [{
        "entity": "John Watson", "fact": "wound", "disputed": True,
        "books": {"Scarlet": "shoulder", "Sign": "leg"},
        "passages": {"Scarlet": [{"chapter": "Ch 1", "quote": "quote in Scarlet"}],
                     "Sign": [{"chapter": "Ch 1", "quote": "quote in Sign"}]},
    }]  # fmt: skip


def test_every_audit_explorer_offers_both_views(tmp_path):
    web, svc = _shop(tmp_path)
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    page = web.get(f"/jobs/{job}/bible-grid.html").text
    assert (
        'data-testid="view-matrix"' in page
        and '"books": ["The Harbour", "The Wreck", "The Return"]' in page
    )


def test_a_browser_gets_a_page_for_an_error_and_a_script_still_gets_json(tmp_path):
    web, _svc = _shop(tmp_path)
    html = {"accept": "text/html,application/xhtml+xml"}
    page = web.get("/jobs/not-a-job", headers=html)
    assert (
        page.status_code == 404
        and 'data-testid="error"' in page.text
        and "Preflight home" in page.text
    )
    refused = web.post("/jobs/not-a-job/refund", headers=html)
    assert refused.status_code in (400, 404) and 'data-testid="error"' in refused.text
    missing = web.post("/quote", headers=html, data={})
    assert missing.status_code == 422 and "series name" in missing.text
    assert web.get("/jobs/not-a-job").json() == {"detail": "Not Found"}  # no Accept: JSON
    assert web.get("/jobs/not-a-job", headers={"accept": "application/json"}).json()["detail"]


def test_pasted_paypal_values_with_stray_whitespace_still_work():
    """Regression: a key file with Windows line endings gave "sandbox\\r" and a broken client."""
    from canonkeeper.preflight.app import make_paypal

    pp = make_paypal({"PAYPAL_MODE": "sandbox\r", "PAYPAL_CLIENT_ID": " cid\r\n",
                      "PAYPAL_CLIENT_SECRET": '"sec"\r'})  # fmt: skip
    assert pp.mode == "sandbox" and pp.client_id == "cid"
    import pytest

    with pytest.raises(RuntimeError):
        make_paypal({"PAYPAL_MODE": "sandbx", "PAYPAL_CLIENT_ID": "c", "PAYPAL_CLIENT_SECRET": "s"})


def test_the_budget_page_offers_paypals_buttons_and_confirms_as_json(tmp_path):
    from canonkeeper.preflight.paypal import FakePayPal

    class Sandboxish(FakePayPal):
        mode, client_id = "sandbox", "test-client-id"

    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=Sandboxish(), workdir=tmp_path / "pf")
    web, svc = TestClient(app), app.state.mcp_service
    b = svc.open_budget("10")
    page = web.get(f"/budget/{b['budget_id']}").text
    order = svc.budgets.get(b["budget_id"]).order_id
    assert 'data-testid="paypal-buttons"' in page and f'return "{order}";' in page
    assert "Or approve on PayPal" in page
    j = {"accept": "application/json"}
    assert (
        web.post(f"/budget/{b['budget_id']}/check", headers=j).json()["problem"]
        == "not approved yet"
    )
    svc.paypal.approve(order)
    done = web.post(f"/budget/{b['budget_id']}/check", headers=j).json()
    assert done == {"status": "active", "problem": None, "budget_url": f"/budget/{b['budget_id']}"}


def test_every_response_carries_the_security_headers(tmp_path):
    web, _svc = _shop(tmp_path)
    for path in ("/", "/sample", "/healthz", "/jobs/nope"):
        h = web.get(path, headers={"accept": "text/html"}).headers
        csp = h["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "https://*.paypal.com" in csp.split("script-src")[1].split(";")[0]  # PayPal SDK
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "strict-transport-security" not in h  # only on https deployments


def test_an_https_deployment_also_sends_hsts(tmp_path):
    from canonkeeper.preflight.paypal import FakePayPal

    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(settings=settings, paypal=FakePayPal(), workdir=tmp_path / "pf",
                     public_base_url="https://preflight.example.com")  # fmt: skip
    assert "max-age" in TestClient(app).get("/healthz").headers["strict-transport-security"]


def test_a_quote_has_its_own_address_so_reloading_it_is_safe(tmp_path):
    """Judge review: /quote-demo was POST-only, so a reload (or a phone coming back to the tab)
    showed "Method Not Allowed", and re-sending the form made a second job."""
    web, svc = _shop(tmp_path)
    r = web.post("/quote-demo", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    url = r.headers["location"]
    first, again = web.get(url), web.get(url)
    assert first.status_code == again.status_code == 200
    assert "Pay $5.05 with PayPal" in again.text and ">quote ready</span>" in again.text
    assert len(svc.store.all()) == 1


def test_uploads_are_checked_on_the_server_not_only_by_the_file_picker(tmp_path):
    """Judge review: an empty .txt was quoted at $5.00 for 0 words, and a .docx was accepted."""
    import pytest

    web, svc = _shop(tmp_path)
    empty = web.post(
        "/quote", data={"series_name": "S"}, files=[("files", ("01-a.txt", b"  \n", "text/plain"))]
    )
    assert empty.status_code == 400 and "01-a.txt is empty" in empty.text
    docx = web.post(
        "/quote",
        data={"series_name": "S"},
        files=[("files", ("01-a.pdf", b"Ann has green eyes.", "application/pdf"))],
    )
    assert docx.status_code == 400 and "not a .docx, .txt or .md file" in docx.text
    with pytest.raises(ValueError, match="is empty"):
        svc.quote_series("S", [{"name": "01-a.txt", "text": ""}])
    assert svc.store.all() == []


def test_versioned_scripts_are_cached_for_good_and_other_static_files_for_an_hour(tmp_path):
    """The 500 KB grid and chart scripts carry their version in their names: a judge's second
    page view should not fetch them again."""
    from canonkeeper.config import Settings
    from canonkeeper.preflight.app import create_app
    from canonkeeper.preflight.paypal import FakePayPal

    app = create_app(
        settings=Settings(provider="mock", workdir=tmp_path / "w", _env_file=None),
        paypal=FakePayPal(),
        workdir=tmp_path / "pf",
    )
    web = TestClient(app)
    grid = web.get("/static/ag-grid-community-36.2.0.min.js")
    assert grid.headers["cache-control"] == "public, max-age=31536000, immutable"
    clip = web.get("/static/assistant-budget.jpg")
    assert clip.headers["cache-control"] == "public, max-age=3600"
    assert "cache-control" not in web.get("/").headers  # pages are never cached
