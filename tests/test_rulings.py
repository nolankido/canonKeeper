"""T14: the author rules on what the model left to them, and the report carries the ruling and a
link back to its job page. No money moves."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from canonkeeper.config import Settings
from canonkeeper.demo import BOOK1, BOOK2, BOOK3
from canonkeeper.preflight.app import create_app
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


def _done_with_ambiguous(svc) -> tuple[str, int]:
    """A finished audit of the demo series, and the index of the item it leaves to the author
    (Ivy Marlowe's hair: red in book 1, grey in book 3)."""
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    assert svc.store.get(job).status == "done"
    report = svc.store.dir(job) / "reports" / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    ambiguous = [i for i, f in enumerate(data["findings"]) if f["verdict"] == "ambiguous"]
    assert len(ambiguous) == 1
    return job, ambiguous[0]


def test_the_report_links_back_to_its_job_page(shop):
    web, svc = shop
    job, _ = _done_with_ambiguous(svc)
    assert f'href="/jobs/{job}" data-testid="back-to-job"' in web.get(f"/jobs/{job}/report").text


def test_the_author_rules_on_an_item_and_the_report_shows_it(shop):
    web, svc = shop
    job, i = _done_with_ambiguous(svc)
    page = web.get(f"/jobs/{job}").text
    assert 'data-testid="judgement"' in page and f'data-testid="rule-error-{i}"' in page

    r = web.post(
        f"/jobs/{job}/findings/{i}/rule",
        data={"verdict": "error", "note": "she has no nickname"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].endswith("#judgement")
    page = web.get(f"/jobs/{job}").text
    assert f'data-testid="ruling-{i}"' in page and "1 of 1 ruled" in page
    report = web.get(f"/jobs/{job}/report").text
    assert "The author ruled: a real error to fix." in report and "she has no nickname" in report
    assert "1 of 1 ruled" in report

    web.post(f"/jobs/{job}/findings/{i}/rule", data={"verdict": "fine"})  # changed their mind
    report = web.get(f"/jobs/{job}/report").text
    assert "The author ruled: not a problem." in report and "a real error to fix" not in report
    assert len(svc.store.get(job).rulings) == 1
    assert svc.paypal.get_order(svc.store.get(job).order_id).raw.get("partial_refunds") is None


def test_rulings_and_guarantee_marks_live_together_on_the_report(shop):
    web, svc = shop
    job, i = _done_with_ambiguous(svc)
    first = svc.get_findings(job)["findings"][0]["index"]
    svc.mark_finding_wrong(job, first, "a disguise")
    assert svc.rule_on_finding(job, i, "fine", "a promotion")["ruled"] is True
    report = web.get(f"/jobs/{job}/report").text
    assert "The author marked this call wrong." in report and "a disguise" in report
    assert "The author ruled: not a problem." in report and "a promotion" in report
    rows = {f["index"]: f for f in svc.get_findings(job, include_cleared=True)["findings"]}
    assert rows[i]["author_review"] == {"verdict": "fine", "note": "a promotion"}


def test_only_ambiguous_items_of_finished_audits_take_a_ruling(shop):
    web, svc = shop
    quoted = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    assert "finished" in svc.rule_on_finding(quoted, 0, "fine")["reason"]
    job, i = _done_with_ambiguous(svc)
    first = svc.get_findings(job)["findings"][0]["index"]
    assert "no such item" in svc.rule_on_finding(job, first, "fine")["reason"]  # a contradiction
    assert "one of" in svc.rule_on_finding(job, i, "maybe")["reason"]
    assert web.post(f"/jobs/{job}/findings/999/rule", data={"verdict": "fine"}).status_code == 409
    assert svc.store.get(job).rulings == []


def test_the_demo_series_gives_the_author_something_to_rule_on_and_reads_like_an_editor(shop):
    """Judge rehearsal: the demo had nothing for "needs your judgement", every explanation was
    the same sentence, and a draft check's fix said to change "the later passage"."""
    from canonkeeper.demo import FOR_THE_AUTHOR, PLANTED

    _web, svc = shop
    job, i = _done_with_ambiguous(svc)
    rows = svc.get_findings(job, include_cleared=True)["findings"]
    assert [r["subject"] for r in rows if r["verdict"] == "ambiguous"] == [FOR_THE_AUTHOR[0][0]]
    explanations = [r["explanation"] for r in rows if r["verdict"] == "contradiction"]
    assert len(explanations) == len(PLANTED) == len(set(explanations))  # one per finding
    assert any("green in The Harbour but blue in The Return" in e for e in explanations)
    assert svc.rule_on_finding(job, i, "fine", "ten years on")["ruled"] is True

    draft = svc.demo_draft()
    check = svc.check_draft(job, draft["name"], draft["text"])["job_id"]
    svc.paypal.approve(svc.order_audit(check)["order_id"])
    svc.confirm_payment(check)
    svc.runner.wait(check, timeout=60)
    fixes = [r["suggested_fix"] for r in svc.get_findings(check)["findings"]]
    assert fixes and all("draft" in f for f in fixes)
