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
  open_budget(amount_usd)                  → the person funds a budget once, on PayPal
  confirm_budget(budget_id)                → captures it; the budget becomes spendable
  pay_with_budget(job_id, budget_key)      → pays a quoted job from it, never past the balance
  budget_status(budget_key) / close_budget(budget_key) → the ledger; refund the unused rest
  request_upload_link(series_name)         → a page where the person uploads the books
  upload_status(token)                     → the job id and price once they have
  list_jobs()                              (stdio only: a hosted server lists nobody's jobs)

Hosted: `canonkeeper serve` also serves these tools over streamable HTTP at /mcp, sharing the
web service's jobs, so an assistant connects to the public URL and holds no keys.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from canonkeeper.config import Settings, load_settings
from canonkeeper.models import Candidate
from canonkeeper.preflight.budgets import BudgetStore
from canonkeeper.preflight.jobs import JobStore, Runner
from canonkeeper.preflight.payments import (
    OPEN,
    QuotaShort,
    close_budget,
    mark_finding_wrong,
    open_budget,
    place_order,
    refund,
    release_unapproved_order,
    settle,
    settle_budget,
    spend_budget,
)
from canonkeeper.preflight.paypal import FakePayPal, PayPal
from canonkeeper.preflight.pricing import CURRENCY
from canonkeeper.preflight.uploads import UploadLinks
from canonkeeper.report.display import headline

PAY_NEXT = (
    "pay it: pay_with_budget(job_id, budget_key) if the person has given you a Preflight "
    "budget; otherwise order_audit(job_id), and the PERSON approves the PayPal payment. "
    "Nothing runs until it is paid."
)


def _status_next(status: str) -> str:
    return {
        "ordered": "waiting for the person to approve the PayPal payment; then "
        "confirm_payment(job_id). Or pay_with_budget(job_id, budget_key) instead.",
        "cancelled": "the checkout was cancelled; pay_with_budget or order_audit again.",
        "held": "PayPal holds the payment until it clears; ask audit_status again later.",
        "paid": "queued; poll audit_status(job_id).",
        "running": "running; poll audit_status(job_id) (progress_done of progress_total).",
        "done": "get_findings(job_id); report_url is the full report.",
        "failed": "retry from the job page, or refund_failed_audit(job_id).",
    }.get(status, "")


class PreflightService:
    """Transport-agnostic implementation; the MCP server and tests call these directly."""

    def __init__(
        self,
        settings: Settings,
        paypal: PayPal,
        workdir: Path,
        *,
        public_base_url: str = "http://127.0.0.1:8787",
        store: JobStore | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.settings = settings
        self.paypal = paypal
        # Hosted inside the web service, the MCP tools share its store and runner: the agent is
        # one more customer of the same shop, and holds no keys of its own.
        self.store = store or JobStore(workdir / "jobs")
        self.runner = runner or Runner(self.store, settings)
        self.uploads = UploadLinks(workdir / "uploads")
        self.budgets = BudgetStore(workdir / "budgets")
        self.base = public_base_url.rstrip("/")

    def request_upload_link(self, series_name: str | None = None) -> dict[str, Any]:
        """A page where the person uploads the books themselves: a whole series does not belong
        in a tool call, and the files are theirs."""
        if not self.settings.can_run_models:
            raise ValueError("this service has no model key configured; it cannot run audits")
        token = self.uploads.create(series_name)
        return {
            "upload_url": f"{self.base}/upload/{token}",
            "token": token,
            "expires_in_hours": 24,
            "next": "give upload_url to the person; they upload the books (one file per book) "
            "and see the price; then call upload_status(token) for the job id and quote",
        }

    # -- agent budgets ------------------------------------------------------------------------

    def open_budget(self, amount_usd: str | float) -> dict[str, Any]:
        try:
            amount = Decimal(str(amount_usd))
        except InvalidOperation:
            raise ValueError("amount_usd must be a number of dollars, e.g. 20") from None
        budget, key = open_budget(amount, self.paypal, self.budgets, self.base, absolute_links=True)
        return {
            "budget_id": budget.id,
            "budget_key": key,
            "amount": f"{budget.amount_usd} {CURRENCY}",
            "approve_url": budget.approve_url,
            "budget_url": f"{self.base}/budget/{budget.id}",
            "next": "keep budget_key private (it spends the budget); ask the person to open "
            "approve_url and approve the payment, and give them budget_url (where they see "
            "every spend and can close the budget); then confirm_budget(budget_id)",
        }

    def confirm_budget(self, budget_id: str) -> dict[str, Any]:
        budget = self.budgets.get(budget_id)
        if budget is None:
            raise ValueError("unknown budget")
        problem = settle_budget(budget, self.paypal, self.budgets)
        fresh = self.budgets.get(budget_id) or budget
        out: dict[str, Any] = {
            "budget_id": budget_id,
            "status": fresh.status,
            "balance": f"{fresh.balance:.2f}",
        }
        if problem:
            out["problem"] = problem
        return out

    def budget_status(self, budget_key: str) -> dict[str, Any]:
        budget = self.budgets.by_key(budget_key)
        if budget is None:
            raise ValueError("unknown budget key")
        return budget.public()

    def pay_with_budget(self, job_id: str, budget_key: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        released = release_unapproved_order(job, self.paypal, self.store)
        try:
            problem = spend_budget(budget_key, job, self.store, self.budgets, self.runner)
        except QuotaShort as short:
            return {"job_id": job_id, "paid": False, "reason": str(short)}
        if problem:
            return {"job_id": job_id, "paid": False, "reason": problem}
        budget = self.budgets.by_key(budget_key)
        return {
            "job_id": job_id,
            "paid": True,
            "amount": f"{job.quote_usd} {CURRENCY}",
            "budget_left": f"{budget.balance:.2f}" if budget else None,
            **(
                {
                    "replaced_order": "the PayPal order the person had not approved is void; "
                    "approving it now charges nothing"
                }
                if released
                else {}
            ),
            "next": "poll audit_status(job_id), then get_findings(job_id)",
        }

    def close_budget(self, budget_key: str) -> dict[str, Any]:
        budget = self.budgets.by_key(budget_key)
        if budget is None:
            raise ValueError("unknown budget key")
        problem = close_budget(budget, self.paypal, self.budgets)
        fresh = self.budgets.get(budget.id) or budget
        out = fresh.public()
        if problem:
            out["problem"] = problem
        return out

    def mark_finding_wrong(self, job_id: str, finding_index: int, note: str = "") -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        problem, amount = mark_finding_wrong(
            job, finding_index, note, self.paypal, self.store, self.budgets
        )
        if problem:
            return {"job_id": job_id, "marked": False, "reason": problem}
        return {"job_id": job_id, "marked": True, "given_back": f"{amount:.2f} {CURRENCY}"}

    def rule_on_finding(
        self, job_id: str, finding_index: int, ruling: str, note: str = ""
    ) -> dict[str, Any]:
        from canonkeeper.preflight.rulings import RULINGS, rule_on_finding

        problem = rule_on_finding(self.store, job_id, finding_index, ruling, note)
        if problem:
            return {"job_id": job_id, "ruled": False, "reason": problem}
        return {"job_id": job_id, "ruled": True, "ruling": f"{ruling}: {RULINGS[ruling]}"}

    def upload_status(self, token: str) -> dict[str, Any]:
        try:
            record = self.uploads.get(token)
        except KeyError:
            raise ValueError("unknown or expired upload link") from None
        job_id = record.get("job_id")
        job = self.store.get(job_id) if job_id else None
        if job is None:
            return {"token": token, "status": "waiting for the upload"}
        return {
            "token": token,
            "status": "quoted" if job.status == "quoted" else job.status,
            "job_id": job.id,
            "series": job.series_name,
            "books": job.books,
            "price": {"amount": job.quote_usd, "currency": CURRENCY},
            "next": PAY_NEXT if job.status == "quoted" else _status_next(job.status),
        }

    def quote_series(self, series_name: str, books: list[dict[str, str]]) -> dict[str, Any]:
        """`books`: [{"name": "01-first.txt", "text": "..."}, …] in reading order."""
        if not self.settings.can_run_models:
            raise ValueError("this service has no model key configured; it cannot run audits")
        if not books:
            raise ValueError("at least one book is required")
        files = []
        for i, b in enumerate(books, 1):
            name = (
                (b.get("name") or b.get("title") or b.get("filename"))
                if isinstance(b, dict)
                else None
            )
            if not name or "text" not in b:
                raise ValueError(
                    f'book {i} needs "name" (a file name such as "01-first.txt"; books are '
                    'read in name order) and "text" (the whole book), e.g. '
                    '{"name": "01-first.txt", "text": "..."}'
                )
            files.append((str(name), str(b["text"]).encode("utf-8")))
        job, q = self.store.create_from_upload(series_name, files)
        return {
            "job_id": job.id,
            "series": job.series_name,
            "books": job.books,
            "tokens": job.tokens,
            "words": job.words,
            "price": {"amount": q.total_str, "currency": CURRENCY, "explanation": q.explanation},
            "next": PAY_NEXT,
        }

    def quote_demo_series(self) -> dict[str, Any]:
        """The invented three-book Saltmarsh Chronicles (five planted continuity errors), so the
        whole flow can be tried without a series of one's own."""
        from canonkeeper.demo import BOOK1, BOOK2, BOOK3, SERIES_NAME

        return self.quote_series(
            SERIES_NAME,
            [
                {"name": "01-the-harbour.txt", "text": BOOK1},
                {"name": "02-the-wreck.txt", "text": BOOK2},
                {"name": "03-the-return.txt", "text": BOOK3},
            ],
        )

    def demo_draft(self) -> dict[str, Any]:
        """The demo series' next book, a draft with planted slips, for check_draft."""
        from canonkeeper.demo import DRAFT4

        return {"name": "04-the-ledger.txt", "text": DRAFT4}

    def order_audit(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        try:
            job = place_order(
                job, self.paypal, self.store, self.runner, self.base, absolute_links=True
            )
        except QuotaShort as short:
            return {"job_id": job.id, "status": job.status, "ordered": False, "reason": str(short)}
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
        if problem and job.status not in OPEN:
            raise ValueError(
                f"job is {job.status}: there is no PayPal order to confirm. "
                + (
                    "Pay with pay_with_budget(job_id, budget_key), or order_audit(job_id) first."
                    if job.status == "quoted"
                    else ""
                )
            )
        if problem:  # e.g. "held": captured, PayPal still clearing it; ask again later
            return {"job_id": job.id, "status": job.status, "paid": False, "detail": problem}
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
        shown = ("id", "kind", "series_name", "books", "draft", "parent_id", "status", "quote_usd",
                 "tokens", "words", "created_at", "paid_at", "stage", "progress_done",
                 "progress_total", "claims", "contradictions", "error", "order_id", "budget_id",
                 "refund_id")  # fmt: skip
        data = asdict(job)
        out = {k: data[k] for k in shown if k in data}
        out["job_id"] = job.id
        if job.status == "ordered":
            out["approve_url"] = job.approve_url
        out["next"] = PAY_NEXT if job.status == "quoted" else _status_next(job.status)
        if job.status == "done":
            out["report_url"] = f"{self.base}/jobs/{job.id}/report"
            out["bible_url"] = f"{self.base}/jobs/{job.id}/bible"
            out["findings_json_url"] = f"{self.base}/jobs/{job.id}/download/report.json"
        return out

    def check_draft(self, job_id: str, name: str, text: str) -> dict[str, Any]:
        """Quote a check of one new draft against a finished audit (`job_id`)."""
        if not self.settings.can_run_models:
            raise ValueError("this service has no model key configured; it cannot run audits")
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
            "next": PAY_NEXT,
        }

    def refund_failed_audit(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise ValueError("unknown job")
        problem = refund(job, self.paypal, self.store, self.budgets)
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
        if job.status != "done" or not p.exists():
            return {
                "job_id": job_id,
                "status": job.status,
                "finished": False,
                "findings": None,
                "note": f"no findings yet: the job is {job.status}. "
                + (PAY_NEXT if job.status == "quoted" else _status_next(job.status)),
            }
        report = json.loads(p.read_text(encoding="utf-8"))
        rows = []
        for index, f in enumerate(report["findings"]):
            if f["verdict"] != "contradiction" and not include_cleared:
                continue
            rows.append(
                {
                    "index": index,  # what mark_finding_wrong and rule_on_finding take
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
                    "author_review": f.get("author_review"),
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


# What each tool does to the world, for assistants that ask before acting (MCP tool annotations):
# read-only, destructive (money spent or files gone for good), idempotent, and whether it talks
# to PayPal. Titles are what an assistant shows the person.
TOOL_HINTS: dict[str, tuple[str, dict[str, bool]]] = {
    "request_upload_link": ("Get an upload link for the books", {}),
    "upload_status": ("Has the person uploaded?", {"ro": True}),
    "open_budget": ("Open a PayPal-funded budget", {"world": True}),
    "confirm_budget": ("Confirm the budget's PayPal payment", {"idem": True, "world": True}),
    "mark_finding_wrong": ("Mark a finding wrong (part refund)", {"world": True}),
    "rule_on_finding": ("Record the author's ruling", {"idem": True}),
    "budget_status": ("Budget balance and receipts", {"ro": True}),
    "pay_with_budget": ("Pay for a job from the budget", {"destroy": True}),
    "close_budget": ("Close the budget, refund the rest", {"destroy": True, "world": True}),
    "quote_demo_series": ("Quote the demo series", {}),
    "get_demo_draft": ("Get the demo series' next draft", {"ro": True}),
    "quote_series": ("Quote an audit from book texts", {}),
    "order_audit": ("Create the PayPal order for a quote", {"world": True}),
    "confirm_payment": ("Confirm the PayPal payment", {"idem": True, "world": True}),
    "audit_status": ("Audit status", {"ro": True}),
    "get_findings": ("Read the findings", {"ro": True}),
    "check_draft": ("Quote a check of a new draft", {}),
    "delete_job": ("Delete the books and everything derived", {"destroy": True}),
    "refund_failed_audit": ("Refund a failed audit", {"destroy": True, "world": True}),
    "list_jobs": ("List jobs", {"ro": True}),
}


def _annotations(name: str):  # noqa: ANN202 - ToolAnnotations, imported lazily with the SDK
    from mcp.types import ToolAnnotations

    title, h = TOOL_HINTS[name]
    return ToolAnnotations(
        title=title,
        read_only_hint=h.get("ro", False),
        destructive_hint=h.get("destroy", False),
        idempotent_hint=h.get("idem", h.get("ro", False)),
        open_world_hint=h.get("world", False),
    )


def build_server(service: PreflightService, *, hosted: bool = False):  # noqa: ANN201 - MCPServer type varies by SDK version
    """`hosted`: served over HTTP by the public web service. Then nothing lists other people's
    jobs; every job is reached only by its unguessable id, as on the website."""
    import functools

    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    training = (
        " This instance runs on a free model tier that may use submitted text for training: "
        "send only public-domain text or the demo series, unless the person says otherwise."
        if service.settings.trains_on_prompts
        else ""
    )
    server = MCPServer(
        name="preflight",
        instructions=(
            "Preflight audits a fiction series for continuity errors (every contradiction with "
            "both passages and a fix to the later book) and is paid per audit through PayPal. "
            "1. Books: request_upload_link (the person uploads; then upload_status(token)), or "
            "quote_series with the texts, or quote_demo_series to try it on an invented series. "
            "2. Pay, one of two ways. A budget (best when you will buy more than once): "
            "open_budget → the PERSON approves its PayPal payment at approve_url → "
            "confirm_budget → pay_with_budget(job_id, budget_key) for each audit or draft check, "
            "never past the balance; close_budget refunds the rest. Or per job: order_audit → "
            "the PERSON approves at approve_url → confirm_payment. Never approve a payment on "
            "the person's behalf. 3. Poll audit_status, then get_findings. 4. For the next "
            "book: check_draft(job_id, name, text), then pay as above. Nothing is sent to a "
            "model before payment is captured." + training
        ),
    )

    def tool(**kw: Any):  # noqa: ANN202
        """Register a tool whose expected failures reach the assistant with their reason (the
        SDK shows only "Error executing tool" for any other exception)."""

        def register(fn):  # noqa: ANN001, ANN202
            @functools.wraps(fn)
            def explained(*a: Any, **k: Any) -> Any:
                try:
                    return fn(*a, **k)
                except (ValueError, KeyError, LookupError) as e:
                    raise ToolError(str(e.args[0]) if e.args else type(e).__name__) from e

            return server.tool(annotations=_annotations(fn.__name__), **kw)(explained)

        return register

    @tool(
        description="Get a link where the person uploads their books (one file per book) and "
        "sees the price. Use this for a whole series; then call upload_status(token)."
    )
    def request_upload_link(series_name: str | None = None) -> dict[str, Any]:
        return service.request_upload_link(series_name)

    @tool(
        description="Whether the person has uploaded the books yet; the job id and price once they have."
    )
    def upload_status(token: str) -> dict[str, Any]:
        return service.upload_status(token)

    @tool(
        description="Ask the person to fund a budget (5 to 200 USD) you may spend on audits and "
        "draft checks without asking each time. Returns a PayPal approval URL for the PERSON, "
        "their budget page, and a private budget_key for you."
    )
    def open_budget(amount_usd: str | float) -> dict[str, Any]:
        return service.open_budget(amount_usd)

    @tool(
        description="After the person approved the budget's payment: capture it and make the "
        "budget spendable."
    )
    def confirm_budget(budget_id: str) -> dict[str, Any]:
        return service.confirm_budget(budget_id)

    @tool(
        description="When the person says a reported contradiction is wrong: note it on the "
        "report and give part of the price back (0.50 each, at most half the price). "
        "finding_index is the finding's `index` from get_findings."
    )
    def mark_finding_wrong(job_id: str, finding_index: int, note: str = "") -> dict[str, Any]:
        return service.mark_finding_wrong(job_id, finding_index, note)

    @tool(
        description="Record the author's ruling on an item the audit left to them ('needs your "
        "judgement': get_findings with include_cleared, verdict 'ambiguous'). ruling: 'error' (a "
        "real slip to fix) or 'fine' (not a problem). Free; shown on the report; can be changed. "
        "Only rule as the author tells you: never decide for them."
    )
    def rule_on_finding(
        job_id: str, finding_index: int, ruling: str, note: str = ""
    ) -> dict[str, Any]:
        return service.rule_on_finding(job_id, finding_index, ruling, note)

    @tool(description="A budget's balance and every spend, credit and refund.")
    def budget_status(budget_key: str) -> dict[str, Any]:
        return service.budget_status(budget_key)

    @tool(
        description="Pay a quoted job (an audit or a draft check) from the budget and start it. "
        "Refused if the balance does not cover the price. A PayPal order the person has not "
        "approved yet is voided and replaced by the budget payment; an approved one is not."
    )
    def pay_with_budget(job_id: str, budget_key: str) -> dict[str, Any]:
        return service.pay_with_budget(job_id, budget_key)

    @tool(
        description="Close the budget: no more spending; the unused balance is refunded to the "
        "person's PayPal."
    )
    def close_budget(budget_key: str) -> dict[str, Any]:
        return service.close_budget(budget_key)

    @tool(
        description="Quote an audit of the Saltmarsh Chronicles, an invented three-book demo "
        "series with five planted continuity errors and one item for the author to judge, to try "
        "the whole flow (pay, findings, "
        "draft check) without a series of the person's own."
    )
    def quote_demo_series() -> dict[str, Any]:
        return service.quote_demo_series()

    @tool(
        description="The demo series' next book, a draft with planted slips: pass its name and "
        "text to check_draft with the finished demo audit's job_id."
    )
    def get_demo_draft() -> dict[str, Any]:
        return service.demo_draft()

    @tool(
        description='Price an audit from the book texts. books: a list of {"name": "01-first.txt", '
        '"text": "<the whole book>"} read in name order. Returns a job id and the price with '
        "its arithmetic. For a whole series the person has as files, request_upload_link is "
        "easier." + training
    )
    def quote_series(series_name: str, books: list[dict[str, str]]) -> dict[str, Any]:
        return service.quote_series(series_name, books)

    @tool(
        description="Create the PayPal order for a quoted job. Returns the approval URL the person must open."
    )
    def order_audit(job_id: str) -> dict[str, Any]:
        return service.order_audit(job_id)

    @tool(
        description="After the person approved on PayPal, capture the payment and start the audit."
    )
    def confirm_payment(job_id: str) -> dict[str, Any]:
        return service.confirm_payment(job_id)

    @tool(
        description="Current status of a job (quoted/ordered/paid/running/done/failed) and report links when done."
    )
    def audit_status(job_id: str) -> dict[str, Any]:
        return service.audit_status(job_id)

    @tool(
        description="The audit's findings as data: each contradiction with its quotes, explanation and suggested fix."
    )
    def get_findings(job_id: str, include_cleared: bool = False) -> dict[str, Any]:
        return service.get_findings(job_id, include_cleared=include_cleared)

    @tool(
        description=(
            "Quote a check of the author's next draft against a finished series audit (job_id): "
            "only the places where the draft disagrees with the published books, each with a fix "
            "to the draft. Pay with pay_with_budget, or order_audit and the person approves."
        )
        + training
    )
    def check_draft(job_id: str, name: str, text: str) -> dict[str, Any]:
        return service.check_draft(job_id, name, text)

    @tool(
        description=(
            "Delete the job's manuscripts, bible, reports and cached model responses, keeping "
            "only a receipt. Only when the person asks for it; it cannot be undone."
        )
    )
    def delete_job(job_id: str) -> dict[str, Any]:
        return service.delete_job(job_id)

    @tool(
        description=(
            "Refund the full payment for an audit that failed (status 'failed'), back to the "
            "person's PayPal account. Only when the person asks for it instead of a retry."
        )
    )
    def refund_failed_audit(job_id: str) -> dict[str, Any]:
        return service.refund_failed_audit(job_id)

    if not hosted:

        @tool(description="List jobs with status and price.")
        def list_jobs() -> list[dict[str, Any]]:
            return service.list_jobs()

    return server


PUBLIC_URL_FILE = "public_url"  # written by `serve` into its preflight dir; read by `mcp`


def public_url_for(workdir: Path, explicit: str | None = None) -> str:
    """Where approval links and report links should point: the flag, else PREFLIGHT_PUBLIC_URL,
    else whatever URL the web service sharing this workdir announced when it started, else the
    default port. Without the third step, `serve --port 8799` plus `mcp` handed out links to a
    server that was not there."""
    import os

    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("PREFLIGHT_PUBLIC_URL"):
        return os.environ["PREFLIGHT_PUBLIC_URL"].rstrip("/")
    try:
        announced = (workdir / PUBLIC_URL_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        announced = ""
    return announced.rstrip("/") or "http://127.0.0.1:8787"


def make_service(
    settings: Settings | None = None,
    paypal: PayPal | None = None,
    workdir: Path | None = None,
    public_url: str | None = None,
) -> PreflightService:
    from canonkeeper.preflight.app import make_paypal

    settings = settings or load_settings()
    wd = workdir or settings.workdir / "preflight"
    return PreflightService(
        settings,
        paypal or make_paypal(state_dir=wd),
        wd,
        public_base_url=public_url_for(wd, public_url),
    )


__all__ = ["FakePayPal", "PreflightService", "build_server", "make_service", "public_url_for"]
