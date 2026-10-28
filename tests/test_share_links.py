"""Judge review: the job id was the only credential, and the report's own address contains it,
so sharing a report shared the power to mark findings wrong, refund and delete. A finished job
now has a separate read-only link."""

from __future__ import annotations

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


def _done(tmp_path):
    settings = Settings(provider="mock", workdir=tmp_path / "w", _env_file=None)
    app = create_app(
        settings=settings,
        paypal=FakePayPal(),
        workdir=tmp_path / "pf",
        public_base_url="http://preflight.test",
    )
    web, svc = TestClient(app), app.state.mcp_service
    job = svc.quote_series("The Saltmarsh Chronicles", BOOKS)["job_id"]
    svc.paypal.approve(svc.order_audit(job)["order_id"])
    svc.confirm_payment(job)
    svc.runner.wait(job, timeout=60)
    return web, svc, job


def test_a_finished_job_offers_a_read_only_link_without_its_id(tmp_path):
    web, svc, job = _done(tmp_path)
    page = web.get(f"/jobs/{job}").text
    share = svc.store.get(job).share_id
    assert share and share != job
    assert f'value="http://preflight.test/r/{share}"' in page
    assert web.get(f"/jobs/{job}").text.count(share) == 1  # made once, then reused

    r = web.get(f"/r/{share}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/r/{share}/report"
    report = web.get(f"/r/{share}/report").text
    assert 'data-testid="shared"' in report and "Tobias Crane" in report
    assert 'href="bible-grid.html' in report  # the bible, relative to /r/<share>/
    for name in ("report", "bible-grid.html", "bible.html", "bible.csv"):
        body = web.get(f"/r/{share}/{name}").text
        assert job not in body, name  # nothing on a shared page leads back to the owner's key
    assert web.get(f"/r/{share}/report.json").status_code == 404


def test_a_share_link_dies_with_the_files(tmp_path):
    web, svc, job = _done(tmp_path)
    web.get(f"/jobs/{job}")
    share = svc.store.get(job).share_id
    assert web.get(f"/r/{share}/report").status_code == 200
    svc.delete_job(job)
    assert web.get(f"/r/{share}/report").status_code == 404
    assert web.get("/r/not-a-share-id-x/report").status_code == 404
    assert web.get(f"/r/{job}/report").status_code == 404  # a job id is not a share id


def test_the_assistant_gets_the_share_link_to_pass_on(tmp_path):
    web, svc, job = _done(tmp_path)
    st = svc.audit_status(job)
    share = svc.store.get(job).share_id
    assert st["share_url"] == f"http://preflight.test/r/{share}"
    assert web.get(f"/r/{share}/report").status_code == 200


def test_only_the_owners_report_can_rule_from_its_grid(tmp_path):
    web, svc, job = _done(tmp_path)
    owner = web.get(f"/jobs/{job}/report").text
    assert f'var ACTIONS = "/jobs/{job}/findings";' in owner
    web.get(f"/jobs/{job}")
    shared = web.get(f"/r/{svc.store.get(job).share_id}/report").text
    assert "var ACTIONS = null;" in shared
    ambiguous = svc.get_findings(job)["needs_your_judgement"][0]["index"]
    r = web.post(
        f"/jobs/{job}/findings/{ambiguous}/rule",
        data={"verdict": "fine"},
        headers={"accept": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"ruled": True, "verdict": "fine"}
    bad = web.post(
        f"/jobs/{job}/findings/0/rule",
        data={"verdict": "fine"},
        headers={"accept": "application/json"},
    )
    assert bad.status_code == 409 and bad.json()["ruled"] is False
