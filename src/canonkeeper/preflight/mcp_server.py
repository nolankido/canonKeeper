"""Preflight as an MCP server: an author's AI assistant can quote, order, pay for and collect
a continuity audit itself.

    canonkeeper mcp                     # stdio transport, for Claude Desktop / Cursor / etc.

The agentic-commerce shape, deliberately: the agent quotes and creates the PayPal order, the
*human* approves the payment on PayPal's page (the agent hands over the approval link), and
the agent captures, waits and reads the report. No agent ever holds the buyer's credentials.

Tools
  quote_series(series_name, books)         → job id, token count, price with the arithmetic
  order_audit(job_id)                      → PayPal approval URL for the human to open
  confirm_payment(job_id)                  → captures after approval, starts the audit
  audit_status(job_id)                     → quoted / ordered / paid / running / done / failed
  get_findings(job_id)                     → the contradictions and verdicts, as data
  check_draft(job_id, draft)               → quote a check of one new draft against an audit
  refund_failed_audit(job_id)              → full refund, only for a failed audit
  delete_job(job_id)                       → delete the uploads and results, keep a receipt
  list_jobs()
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from canonkeeper.config import Settings, load_settings
from canonkeeper.models import Candidate
from canonkeeper.preflight.jobs import JobStore, Runner
from canonkeeper.preflight.payments import place_order, refund, settle
from canonkeeper.preflight.paypal import FakePayPal, PayPal
from canonkeeper.preflight.pricing import CURRENCY
from canonkeeper.report.display import headline


class PreflightService:
    """Transport-agnostic implementation; the MCP server and tests call these directly."""

    def __init__(
        self,
        settings: Settings,
        paypal: PayPal,
        workdir: Path,
        *,
        public_base_url: str = "http://127.0.0.1:8787",
    ) -> None:
        self.settings = settings
        self.paypal = paypal
        self.store = JobStore(workdir / "jobs")
        self.runner = Runner(self.store, settings)
        self.base = public_base_url.rstrip("/")

    def quote_series(self, series_name: str, books: list[dict[str, str]]) -> dict[str, Any]:
        """`books`: [{"name": "01-first.txt", "text": "..."}, …] in reading order."""
        if not books:
            raise ValueError("at least one book is required")
        files = [(str(b["name"]), str(b["text"]).encode("utf-8")) for b in books]
        job, q = self.store.create_from_upload(series_name, files)
        return {
            "job_id": job.id,
            "series": job.series_name,
            "books": job.books,
            "tokens": job.tokens,
            "words": job.words,
            "price": {"amount": q.total_str, "currency": CURRENCY, "explanation": q.explanation},
            "next": "order_audit(job_id) to create a PayPal order; nothing runs until it is paid",
        }

    def order_audit(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        job = place_order(job, self.paypal, self.store, self.runner, self.base, absolute_links=True)
        if job.status != "ordered":
            return {"job_id": job.id, "status": job.status}
        return {
            "job_id": job.id,
            "status": job.status,
            "order_id": job.order_id,
            "amount": f"{job.quote_usd} {CURRENCY}",
            "approve_url": job.approve_url,
            "next": "ask the person to open approve_url and approve the payment, then call confirm_payment(job_id)",
        }

    def confirm_payment(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        problem = settle(job, self.paypal, self.store, self.runner)
        if problem == "not approved yet":
            return {
                "job_id": job.id,
                "status": "ordered",
                "paid": False,
                "detail": problem,
                "approve_url": job.approve_url,
            }
        if problem and job.status != "ordered":
            raise ValueError(f"job is {job.status}; call order_audit first")
        if problem:
            return {"job_id": job.id, "status": "ordered", "paid": False, "detail": problem}
        if job.status != "paid":  # captured earlier, maybe by the web app's /return
            return {**self.audit_status(job_id), "paid": True}
        return {
            "job_id": job.id,
            "status": "paid",
            "paid": True,
            "next": "poll audit_status(job_id)",
        }

    def audit_status(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        out = {
            k: v for k, v in asdict(job).items() if k != "approve_url" or job.status == "ordered"
        }
        if job.status == "done":
            out["report_url"] = f"{self.base}/jobs/{job.id}/report"
            out["bible_url"] = f"{self.base}/jobs/{job.id}/bible"
            out["findings_json_url"] = f"{self.base}/jobs/{job.id}/download/report.json"
        return out

    def check_draft(self, job_id: str, name: str, text: str) -> dict[str, Any]:
        """Quote a check of one new draft against a finished audit (`job_id`)."""
        try:
            job, q = self.store.create_check(job_id, name, text.encode("utf-8"))
        except KeyError:
            raise ValueError("unknown job") from None
        return {
            "job_id": job.id,
            "checks_against": job_id,
            "draft": job.draft,
            "tokens": job.tokens,
            "price": {"amount": q.total_str, "currency": CURRENCY, "explanation": q.explanation},
            "next": "order_audit(job_id) to create a PayPal order; nothing runs until it is paid",
        }

    def refund_failed_audit(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        problem = refund(job, self.paypal, self.store)
        if problem:
            raise ValueError(problem)
        return {
            "job_id": job.id,
            "status": job.status,
            "refund": {"id": job.refund_id, "amount": f"{job.quote_usd} {CURRENCY}"},
        }

    def delete_job(self, job_id: str) -> dict[str, Any]:
        try:
            job = self.store.purge(job_id)
        except KeyError:
            raise ValueError("unknown job") from None
        return {
            "job_id": job.id,
            "status": job.status,
            "receipt": {"order_id": job.order_id, "amount": job.quote_usd},
        }

    def get_findings(self, job_id: str, *, include_cleared: bool = False) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        p = self.store.dir(job_id) / "reports" / "report.json"
        if not p.exists():
            return {"job_id": job_id, "status": job.status, "findings": []}
        report = json.loads(p.read_text(encoding="utf-8"))
        rows = []
        for f in report["findings"]:
            if f["verdict"] != "contradiction" and not include_cleared:
                continue
            rows.append(
                {
                    "summary": headline(Candidate.model_validate(f["candidate"])),
                    "check": f["candidate"]["heuristic"],
                    "verdict": f["verdict"],
                    "severity": f["severity"],
                    "subject": f["candidate"]["claims"][0]["subject"],
                    "predicate": f["candidate"]["claims"][0]["predicate"],
                    "values": [
                        {
                            "predicate": c["predicate"],
                            "value": c["object"],
                            "book": c["provenance"].get("book_title") or c["provenance"]["book"],
                            "chapter": c["provenance"]["chapter"],
                            "quote": c["provenance"]["quote"],
                        }
                        for c in f["candidate"]["claims"]
                    ],
                    "explanation": f["explanation"],
                    "suggested_fix": f.get("suggested_fix"),
                }
            )
        return {
            "job_id": job_id,
            "series": report["series"],
            "facts": report["claims_total"],
            "candidates": report["candidates_total"],
            "findings": rows,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        return [
            {
                "job_id": j.id,
                "series": j.series_name,
                "status": j.status,
                "price": j.quote_usd,
                "created": j.created_at,
            }
            for j in self.store.all()
        ]


def build_server(service: PreflightService):  # noqa: ANN201 - MCPServer type varies by SDK version
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="preflight",
        instructions=(
            "Preflight audits a fiction series for continuity errors and charges per audit via "
            "PayPal. Flow: quote_series → order_audit → the PERSON approves the payment at the "
            "approve_url (never approve on their behalf) → confirm_payment → poll audit_status "
            "→ get_findings. Nothing is sent to a model before payment is captured."
        ),
    )

    @server.tool(
        description="Price an audit from the book texts. Returns a job id and the quote with its arithmetic."
    )
    def quote_series(series_name: str, books: list[dict[str, str]]) -> dict[str, Any]:
        return service.quote_series(series_name, books)

    @server.tool(
        description="Create the PayPal order for a quoted job. Returns the approval URL the person must open."
    )
    def order_audit(job_id: str) -> dict[str, Any]:
        return service.order_audit(job_id)

    @server.tool(
        description="After the person approved on PayPal, capture the payment and start the audit."
    )
    def confirm_payment(job_id: str) -> dict[str, Any]:
        return service.confirm_payment(job_id)

    @server.tool(
        description="Current status of a job (quoted/ordered/paid/running/done/failed) and report links when done."
    )
    def audit_status(job_id: str) -> dict[str, Any]:
        return service.audit_status(job_id)

    @server.tool(
        description="The audit's findings as data: each contradiction with its quotes, explanation and suggested fix."
    )
    def get_findings(job_id: str, include_cleared: bool = False) -> dict[str, Any]:
        return service.get_findings(job_id, include_cleared=include_cleared)

    @server.tool(
        description=(
            "Quote a check of the author's next draft against a finished series audit: only the "
            "places where the draft disagrees with the published books. Pay with order_audit."
        )
    )
    def check_draft(job_id: str, name: str, text: str) -> dict[str, Any]:
        return service.check_draft(job_id, name, text)

    @server.tool(
        description=(
            "Delete the job's manuscripts, bible, reports and cached model responses, keeping "
            "only a receipt. Only when the person asks for it; it cannot be undone."
        )
    )
    def delete_job(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id)

    @server.tool(
        description=(
            "Refund the full payment for an audit that failed (status 'failed'), back to the "
            "person's PayPal account. Only when the person asks for it instead of a retry."
        )
    )
    def refund_failed_audit(job_id: str) -> dict[str, Any]:
        return service.refund_failed_audit(job_id)

    @server.tool(description="List jobs with status and price.")
    def list_jobs() -> list[dict[str, Any]]:
        return service.list_jobs()

    return server


def make_service(
    settings: Settings | None = None, paypal: PayPal | None = None, workdir: Path | None = None
) -> PreflightService:
    from canonkeeper.preflight.app import make_paypal

    settings = settings or load_settings()
    wd = workdir or settings.workdir / "preflight"
    return PreflightService(
        settings,
        paypal or make_paypal(state_dir=wd),
        wd,
        public_base_url=__import__("os").environ.get(
            "PREFLIGHT_PUBLIC_URL", "http://127.0.0.1:8787"
        ),
    )


__all__ = ["FakePayPal", "PreflightService", "build_server", "make_service"]
