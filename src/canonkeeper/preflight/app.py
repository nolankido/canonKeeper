"""The Preflight web app.

GET  /                 upload form
POST /quote            files + series name → job (quoted) → quote page
POST /jobs/{id}/order  create PayPal order → redirect to approve URL
GET  /return           PayPal sends the buyer back with ?token=<order id> → capture → run
GET  /cancel           buyer backed out
GET  /jobs/{id}        status page (auto-refreshes while running)
GET  /jobs/{id}/report | /bible   the finished HTML pages
GET  /fake-approve     only when PAYPAL mode is "fake": simulates the buyer approving
GET  /healthz
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool

from canonkeeper.config import Settings, load_settings
from canonkeeper.models import AuditReport
from canonkeeper.preflight.budgets import Budget
from canonkeeper.preflight.jobs import (
    MAX_BOOKS,
    MAX_TOTAL_BYTES,
    Job,
    JobStore,
    Runner,
)
from canonkeeper.preflight.mcp_server import PreflightService, build_server
from canonkeeper.preflight.payments import (
    GUARANTEE_CAP,
    GUARANTEE_USD,
    QuotaShort,
    close_budget,
    delete_job_files,
    mark_finding_wrong,
    place_order,
    refund,
    settle,
    settle_budget,
    tighten_budget,
)
from canonkeeper.preflight.paypal import (
    FakePayPal,
    Order,
    PayPal,
    PayPalClient,
)
from canonkeeper.preflight.pricing import CURRENCY, Quote, quote
from canonkeeper.preflight.rulings import RULINGS, judgement_items, rule_on_finding
from canonkeeper.report.display import headline
from canonkeeper.report.render import report_html

_TEMPLATES = Path(__file__).parent / "templates"
_SAMPLES = Path(__file__).parent / "samples"
_STATIC = Path(__file__).parent / "static"
_MY_JOBS, _MY_JOBS_MAX = "preflight_jobs", 10  # cookie: the jobs this browser started
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def paypal_env() -> dict[str, str]:
    """PAYPAL_* settings: `.env` in the working directory, overridden by the real environment
    (the same precedence pydantic-settings gives the rest of the configuration)."""
    from dotenv import dotenv_values

    merged: dict[str, str] = {k: v for k, v in dotenv_values(".env").items() if v is not None}
    merged.update(os.environ)
    return merged


def keep_awake_until(value: str | None) -> datetime | None:
    """PREFLIGHT_KEEP_AWAKE_UNTIL: a date (the whole day, UTC) or an ISO timestamp. Unreadable is
    refused loudly at start-up rather than silently ignored: a judge's job is what it protects."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:  # YYYY-MM-DD: through the end of that day
            return datetime.fromisoformat(text).replace(tzinfo=UTC) + timedelta(days=1)
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise RuntimeError(
            f"PREFLIGHT_KEEP_AWAKE_UNTIL={value!r} is not a date (YYYY-MM-DD) or ISO timestamp"
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _mcp_security(base: str) -> TransportSecuritySettings:
    """A server on localhost keeps the SDK's DNS-rebinding guard (a web page must not drive a
    local server); a public one accepts its own host, which is the only way anyone reaches it."""
    host = urlparse(base).netloc
    local = host.split(":")[0] in ("127.0.0.1", "localhost", "[::1]")
    if local:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host],
        allowed_origins=[f"{urlparse(base).scheme}://{host}"],
    )


def make_paypal(
    settings_env: dict[str, str] | None = None, *, state_dir: Path | None = None
) -> PayPal:
    """`state_dir`: where the fake keeps its orders so `serve` and `mcp` share them."""
    raw = settings_env if settings_env is not None else paypal_env()
    # A value pasted with a trailing space or a Windows line ending ("sandbox\r") breaks every
    # PayPal call with an error that does not say why: keys and modes never contain whitespace.
    env = {k: v.strip().strip("'\"") for k, v in raw.items() if isinstance(v, str)}
    env = {k: v for k, v in env.items() if v}
    mode = env.get("PAYPAL_MODE", "fake" if not env.get("PAYPAL_CLIENT_ID") else "sandbox").lower()
    if mode not in ("fake", "sandbox", "live"):
        raise RuntimeError(f"PAYPAL_MODE must be fake, sandbox or live, not {mode!r}")
    if mode == "fake":
        return FakePayPal(state_path=state_dir / "fake-paypal.json" if state_dir else None)
    cid, secret = env.get("PAYPAL_CLIENT_ID"), env.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required unless PAYPAL_MODE=fake"
        )
    return PayPalClient(cid, secret, mode=mode)


TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".text")


def _refuse_before_reading(files: list[UploadFile]) -> None:
    """Apply the upload limits from the sizes the form already declared, before any file is
    read into memory. (The store checks again on the bytes; this only saves the memory.) The
    page's file picker asks for .txt and .md, but a browser does not enforce that."""
    for f in files:
        name = f.filename or ""
        if not name.lower().endswith(TEXT_SUFFIXES):
            raise HTTPException(
                400,
                f"{name or 'a file'} is not a .txt or .md file: save the book as plain text or "
                "Markdown and upload that",
            )
    if len(files) > MAX_BOOKS:
        raise HTTPException(400, f"at most {MAX_BOOKS} books per series")
    total = sum(f.size or 0 for f in files)
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(
            413, f"upload too large ({total / 1e6:.0f} MB; limit {MAX_TOTAL_BYTES // 10**6} MB)"
        )


STATUS_LABELS = {
    "quoted": "quote ready",
    "ordered": "waiting for PayPal",
    "cancelled": "not paid",
    "held": "payment on hold",
    "paid": "paid · queued",
    "running": "running",
    "done": "done",
    "failed": "failed",
    "refunding": "refunding",
    "refunded": "refunded",
    "deleted": "files deleted",
    "active": "active",
    "closing": "closing",
    "closed": "closed",
}


def chat_markdown(text: str) -> Markup:
    """An assistant's reply as a chat window shows it: escaped, with its bold and italics."""
    t = str(escape(text))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return Markup(t)


def status_label(status: str) -> str:
    """The words a person sees for a job's or a budget's status."""
    return STATUS_LABELS.get(status, status)


# What every response says about itself. The policy admits exactly what the pages load: PayPal's
# JS SDK, its button frames and images; AG Grid from this site or its pinned jsDelivr copy;
# PayPal and Venmo pop-ups open as their own windows, outside this policy. Pages keep their small
# inline scripts and styles. Nothing may frame Preflight (no clickjacking of a pay button).
_PAYPAL = "https://*.paypal.com https://*.paypalobjects.com"
SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'unsafe-inline' {_PAYPAL} https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline'",
            f"img-src 'self' data: {_PAYPAL}",
            f"frame-src {_PAYPAL}",
            f"connect-src 'self' {_PAYPAL}",
            "font-src 'self' data:",
            "object-src 'none'",
            "base-uri 'self'",
            f"form-action 'self' {_PAYPAL}",
            "frame-ancestors 'none'",
        ]
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": 'camera=(), microphone=(), geolocation=(), payment=(self "https://www.paypal.com")',
}

WEBHOOK_EVENTS = ("CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED")


class WebhookId:
    """The PayPal webhook id this deployment verifies notices against: PAYPAL_WEBHOOK_ID if
    set, otherwise found or created through PayPal's API at startup for <public url>/paypal/
    webhook. PayPal only delivers to a public https address, so a local run skips it."""

    def __init__(self, configured: str) -> None:
        self.value = configured.strip()
        self.note = "PAYPAL_WEBHOOK_ID" if not self.value else "configured"

    def register_in_background(self, pp: PayPal, base: str) -> None:
        if self.value or pp.mode == "fake" or not hasattr(pp, "ensure_webhook"):
            return
        host = urlparse(base).hostname or ""
        if not base.startswith("https://") or host in ("localhost", "127.0.0.1", "::1"):
            self.note = "webhooks need a public https address"
            return
        self.note = "registering with PayPal"

        def register() -> None:
            try:
                self.value = pp.ensure_webhook(f"{base}/paypal/webhook", WEBHOOK_EVENTS)
                self.note = "registered"
            except Exception as e:  # PayPal down, or the app lacks webhook permission
                self.note = f"PayPal did not register the webhook ({type(e).__name__})"

        threading.Thread(target=register, name="paypal-webhook", daemon=True).start()


def create_app(
    *,
    settings: Settings | None = None,
    paypal: PayPal | None = None,
    workdir: Path | None = None,
    public_base_url: str | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    workdir = workdir or settings.workdir / "preflight"
    store = JobStore(workdir / "jobs")
    base = (
        public_base_url or os.environ.get("PREFLIGHT_PUBLIC_URL") or "http://127.0.0.1:8787"
    ).rstrip("/")
    runner = Runner(store, settings)
    from canonkeeper.preflight import demo_recording

    # With no key, the demo series is answered from the packaged recording (see Runner._replays).
    demo_recorded = demo_recording.label() if settings.provider == "mock" else None
    if settings.provider != "mock":
        # A host that restarts loses its disk; the demo's recorded answers come back with the
        # package, so a cold instance runs the demo series without spending any quota.
        demo_recording.seed(runner.demo_cache_dir)
    if os.environ.get("RENDER") or os.environ.get("PREFLIGHT_KEEPALIVE"):
        # Render's free plan spins an instance down after ~15 min without inbound requests,
        # running audit or not; a paid job must not die because nobody is watching its page.
        runner.keepalive_url = f"{base}/healthz"
    runner.keep_awake_until = keep_awake_until(os.environ.get("PREFLIGHT_KEEP_AWAKE_UNTIL"))
    runner.ensure_keepalive()
    runner.resume_interrupted()
    runner.watch(on_held=lambda job: settle(job, pp, store, runner))  # see Runner.watch
    pp = paypal or make_paypal(state_dir=settings.workdir / "preflight")
    webhook = WebhookId(os.environ.get("PAYPAL_WEBHOOK_ID", ""))
    webhook.register_in_background(pp, base)

    # The same service over MCP (streamable HTTP at /mcp), on this app's own jobs and PayPal:
    # an author's assistant connects to the public URL as a customer and holds no keys.
    service = PreflightService(
        settings, pp, workdir, public_base_url=base, store=store, runner=runner
    )
    mcp_server = build_server(service, hosted=True)
    mcp_http = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_mcp_security(base),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="Preflight by canonkeeper", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.router.routes.extend(mcp_http.routes)
    app.state.store, app.state.runner, app.state.paypal = store, runner, pp

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if base.startswith("https://"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    app.state.mcp_service = service
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    templates.env.filters["label"] = status_label
    templates.env.filters["chat"] = chat_markdown

    def render(request: Request, name: str, status_code: int = 200, **ctx: object) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {
                "paypal_mode": pp.mode,
                "provider": settings.provider,
                "paypal_client_id": pp.client_id if pp.mode in ("sandbox", "live") else None,
                "demo_recorded": demo_recorded,
                "trains_on_prompts": settings.trains_on_prompts,
                "no_model": None if settings.can_run_models else no_model,
                **ctx,
            },
            status_code=status_code,
        )

    no_model = (
        "This instance has no model key configured, so it cannot run an audit; quotes and "
        "payments are switched off until it does (set GEMINI_API_KEY)."
    )

    def require_model() -> None:
        if not settings.can_run_models:
            raise HTTPException(503, no_model)

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    def _for_a_person(request: Request) -> bool:
        """A browser page load, not a script, PayPal's buttons (fetch), MCP or a webhook."""
        path = request.url.path
        if path.startswith(("/mcp", "/paypal/", "/healthz")) or wants_json(request):
            return False
        return "text/html" in request.headers.get("accept", "")

    _HEADINGS = {400: "Something is missing", 404: "Not found", 409: "Already done",
                 413: "Too large", 422: "Something is missing", 503: "Not available right now"}  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request, exc: StarletteHTTPException
    ) -> HTMLResponse | JSONResponse:
        if not _for_a_person(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        detail = (
            str(exc.detail)
            if exc.detail and exc.detail != "Not Found"
            else ("There is nothing at this address.")
        )
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            status=exc.status_code,
            heading=_HEADINGS.get(exc.status_code, "Something went wrong"),
            message=detail[:1].upper() + detail[1:] + ("" if detail.endswith(".") else "."),
        )

    @app.exception_handler(RequestValidationError)
    async def form_error(
        request: Request, exc: RequestValidationError
    ) -> HTMLResponse | JSONResponse:
        if not _for_a_person(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        missing = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        return render(
            request,
            "error.html",
            status_code=422,
            status=422,
            heading="Something is missing",
            message="The form was sent without "
            + (", ".join(missing).replace("_", " ") if missing else "a required field")
            + ". Go back and fill it in.",
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "ok": "true",
            "paypal": pp.mode,
            "provider": settings.provider,
            "model": "ready" if settings.can_run_models else "no key",
            "webhook": "ready" if webhook.value or pp.mode == "fake" else webhook.note,
        }

    def mine(request: Request) -> list[str]:
        """Job ids this browser started (newest first). Job pages are reachable only by their
        unguessable id, so the home page lists only these: listing the store would show every
        visitor's series to every other visitor, demo deploys included."""
        raw = request.cookies.get(_MY_JOBS, "")
        return [j for j in raw.split(".") if _JOB_ID_RE.fullmatch(j)][:_MY_JOBS_MAX]

    def remember(request: Request, response: RedirectResponse, job_id: str) -> RedirectResponse:
        ids = [job_id, *(j for j in mine(request) if j != job_id)][:_MY_JOBS_MAX]
        response.set_cookie(
            _MY_JOBS, ".".join(ids), max_age=60 * 60 * 24 * 60, httponly=True, samesite="lax"
        )
        return response

    def to_quote(request: Request, job_id: str, *, via_agent: bool = False) -> RedirectResponse:
        """After a form makes a quote, send the browser to the job's own address (POST, then
        redirect, then GET): reloading the quote, or coming back to it on a phone, shows the
        same quote instead of "Method Not Allowed" or a second job."""
        url = f"/jobs/{job_id}" + ("?via=assistant" if via_agent else "")
        return remember(request, RedirectResponse(url, status_code=303), job_id)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        jobs = [j for j in (store.get(i) for i in mine(request)) if j is not None]
        # the home page says it in its own privacy card, so not in the banner too
        return render(request, "index.html", jobs=jobs, sample=_SAMPLES.exists(), privacy_card=True)

    @app.get("/private", response_class=HTMLResponse)
    def private(request: Request) -> HTMLResponse:
        """For an unpublished manuscript: run the same Preflight at home with a local model."""
        return render(request, "private.html")

    @app.get("/assistant", response_class=HTMLResponse)
    async def assistant(request: Request) -> HTMLResponse:
        """How to connect an AI assistant over MCP, with the tools it gets (read from the
        server itself, so the list cannot drift from what the assistant sees)."""

        def tags(a: Any) -> list[str]:
            if a is None:
                return []
            return [
                t
                for t, on in (
                    ("reads only", a.read_only_hint),
                    ("cannot be undone", a.destructive_hint),
                    ("talks to PayPal", a.open_world_hint),
                )
                if on
            ]

        tools = [
            {
                "name": t.name,
                "summary": (t.description or "").split(". ")[0].rstrip(".") + ".",
                "tags": tags(t.annotations),
            }
            for t in await mcp_server.list_tools()
        ]
        session_file = _SAMPLES / "agent-session.json"
        session = (
            json.loads(session_file.read_text(encoding="utf-8")) if session_file.exists() else None
        )
        return render(
            request, "assistant.html", mcp_url=f"{base}/mcp", tools=tools, session=session
        )

    @app.post("/quote-demo")
    def quote_demo(request: Request) -> RedirectResponse:
        """Quote the invented three-book Saltmarsh series (five planted errors), so a visitor
        can walk the whole flow without having a series of their own to hand."""
        require_model()
        from canonkeeper.demo import BOOK1, BOOK2, BOOK3, SERIES_NAME

        files = [
            ("01-the-harbour.txt", BOOK1.encode()),
            ("02-the-wreck.txt", BOOK2.encode()),
            ("03-the-return.txt", BOOK3.encode()),
        ]
        job, _ = store.create_from_upload(SERIES_NAME, files)
        return to_quote(request, job.id)

    @app.get("/sample", response_class=HTMLResponse)
    def sample() -> FileResponse:
        """A finished audit of the six public-domain Holmes volumes: what a buyer gets."""
        p = _SAMPLES / "sherlock-holmes-report.html"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="text/html")

    @app.get("/sample/bible", response_class=HTMLResponse)
    def sample_bible() -> FileResponse:
        """The six Holmes volumes' bible in the grid explorer: 3,963 facts to sort and search."""
        p = _SAMPLES / "sherlock-holmes-bible-grid.html"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="text/html")

    # AG Grid Community (MIT, AG-GRID-LICENSE.txt) for the bible explorer, served from here so
    # the hosted pages do not depend on a CDN.
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    store.prune_unpaid()

    @app.post("/quote")
    async def make_quote(
        request: Request, series_name: str = Form(...), files: list[UploadFile] | None = None
    ) -> RedirectResponse:
        job, _ = await quote_upload(series_name, files)
        return to_quote(request, job.id)

    async def quote_upload(series_name: str, files: list[UploadFile] | None) -> tuple[Job, Quote]:
        files = files or []
        require_model()
        if not files or not any(f.filename for f in files):
            raise HTTPException(400, "upload at least one .txt or .md file")
        named = [f for f in files if f.filename]
        _refuse_before_reading(named)
        payload: list[tuple[str, bytes]] = []
        for f in sorted(named, key=lambda f: f.filename or ""):
            payload.append((f.filename or "", await f.read()))
        await run_in_threadpool(store.prune_unpaid)
        try:
            # tokenising up to 40 MB takes seconds: not on the event loop
            return await run_in_threadpool(store.create_from_upload, series_name, payload)
        except ValueError as e:
            raise HTTPException(413 if "large" in str(e) else 400, str(e)) from None

    def upload_link(token: str) -> dict[str, object]:
        try:
            return service.uploads.get(token)
        except KeyError:
            raise HTTPException(404, "this upload link does not exist or has expired") from None

    @app.get("/upload/{token}", response_class=HTMLResponse)
    def upload_page(request: Request, token: str) -> HTMLResponse:
        """The page an assistant's upload link opens: the person uploads the books here."""
        record = upload_link(token)
        job = store.get(str(record["job_id"])) if record.get("job_id") else None
        return render(request, "upload.html", token=token, record=record, job=job)

    @app.post("/upload/{token}")
    async def upload_books(
        request: Request,
        token: str,
        series_name: str = Form(...),
        files: list[UploadFile] | None = None,
    ) -> RedirectResponse:
        if upload_link(token).get("job_id"):
            raise HTTPException(409, "this upload link has already been used")
        job, _ = await quote_upload(series_name, files)
        try:
            service.uploads.attach(token, job.id)
        except ValueError as e:  # two uploads at once on one link: the first one counts
            raise HTTPException(409, str(e)) from None
        return to_quote(request, job.id, via_agent=True)

    @app.post("/upload/{token}/demo")
    def upload_demo(request: Request, token: str) -> RedirectResponse:
        """The upload link, used for the demo series instead of the person's own books: the
        same way a judge without a series to hand can see the agent flow through."""
        if upload_link(token).get("job_id"):
            raise HTTPException(409, "this upload link has already been used")
        require_model()
        from canonkeeper.demo import BOOK1, BOOK2, BOOK3, SERIES_NAME

        job, _ = store.create_from_upload(
            SERIES_NAME,
            [
                ("01-the-harbour.txt", BOOK1.encode()),
                ("02-the-wreck.txt", BOOK2.encode()),
                ("03-the-return.txt", BOOK3.encode()),
            ],
        )
        try:
            service.uploads.attach(token, job.id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return to_quote(request, job.id, via_agent=True)

    def wants_json(request: Request) -> bool:
        """PayPal's JS SDK buttons call the same routes with fetch and want JSON back."""
        return "application/json" in request.headers.get("accept", "")

    @app.post("/jobs/{job_id}/order", response_model=None)
    def order(request: Request, job_id: str) -> RedirectResponse | HTMLResponse | JSONResponse:
        require_model()
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        try:
            job = place_order(job, pp, store, runner, base)
        except QuotaShort as short:
            if wants_json(request):
                return JSONResponse({"error": str(short)}, status_code=503)
            # Refused before PayPal: no order exists and nothing was charged.
            kind = "check" if job.kind == "check" else "audit"
            q = quote(job.tokens, words=job.words, books=len(job.books), kind=kind)
            return render(
                request,
                "quote.html",
                status_code=503,
                job=job,
                quote=q,
                currency=CURRENCY,
                quota_error=str(short),
            )
        if wants_json(request):
            # The buttons approve the order in PayPal's own window; an order already placed
            # for this job (the buyer closed the window and pressed again) is reused.
            if job.status == "ordered" and job.order_id:
                return JSONResponse({"order_id": job.order_id})
            return JSONResponse(
                {"status": job.status, "job_url": f"/jobs/{job_id}"}, status_code=409
            )
        if job.status == "ordered":
            if not job.approve_url:
                raise HTTPException(502, "PayPal returned no approval link")
            return RedirectResponse(job.approve_url, status_code=303)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/return", response_model=None)
    def paypal_return(request: Request, token: str) -> RedirectResponse | HTMLResponse:
        job = store.by_order(token)
        if job is None:
            raise HTTPException(404, "unknown order")
        problem = settle(job, pp, store, runner, known_approved=True, order_id_hint=token)
        if problem:
            return render(
                request,
                "job.html",
                job=job,
                quote=quote(job.tokens, words=job.words, books=len(job.books)),
                note=problem,
                status_code=402,
            )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/jobs/{job_id}/confirm", response_model=None)
    def confirm(request: Request, job_id: str) -> RedirectResponse | HTMLResponse | JSONResponse:
        """For a buyer who approved on PayPal but never came back (closed the tab, lost
        the connection): ask PayPal, and capture if they did approve."""
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        problem = settle(job, pp, store, runner)
        if wants_json(request):
            return JSONResponse(
                {"ok": not problem, "problem": problem, "job_url": f"/jobs/{job_id}"}
            )
        if problem:
            return render(
                request,
                "job.html",
                job=job,
                quote=quote(job.tokens, words=job.words, books=len(job.books)),
                note=problem,
            )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/paypal/webhook")
    async def paypal_webhook(request: Request) -> JSONResponse:
        """PayPal's own notice that an order was approved or a capture completed: the buyer
        approved and then closed the tab, or the return never arrived. A notice is only a
        prompt to ask PayPal: `settle` reads the order from PayPal and pays the job only on a
        completed capture of the quoted amount, so even a forged notice could not mark a job
        paid. Notices are still verified with PayPal first (PAYPAL_WEBHOOK_ID)."""
        webhook_id = webhook.value
        if pp.mode != "fake" and not webhook_id:
            raise HTTPException(503, f"webhooks are not configured ({webhook.note})")
        body = await request.body()
        ok = await run_in_threadpool(pp.verify_webhook, dict(request.headers), body, webhook_id)
        if not ok:
            raise HTTPException(400, "webhook signature not verified")
        try:
            event = json.loads(body)
        except ValueError:
            raise HTTPException(400, "not JSON") from None
        if not isinstance(event, dict) or not isinstance(event.get("resource") or {}, dict):
            raise HTTPException(400, "not a PayPal event")
        kind = str(event.get("event_type", ""))
        resource = event.get("resource") or {}
        if kind == "CHECKOUT.ORDER.APPROVED":
            order_id = resource.get("id")
        elif kind == "PAYMENT.CAPTURE.COMPLETED":
            extra = resource.get("supplementary_data")
            ids = extra.get("related_ids") if isinstance(extra, dict) else None
            order_id = ids.get("order_id") if isinstance(ids, dict) else None
        else:
            return JSONResponse({"ignored": kind})
        job = await run_in_threadpool(store.by_order, str(order_id or ""))
        if job is None:
            budget = await run_in_threadpool(service.budgets.by_order, str(order_id or ""))
            if budget is not None:
                problem = await run_in_threadpool(settle_budget, budget, pp, service.budgets)
                return JSONResponse(
                    {"budget": budget.id, "settled": not problem, "problem": problem}
                )
            return JSONResponse({"ignored": "unknown order"})
        problem = await run_in_threadpool(
            settle, job, pp, store, runner, order_id_hint=str(order_id)
        )
        return JSONResponse({"job": job.id, "settled": not problem, "problem": problem})

    def budget_or_404(budget_id: str) -> Budget:
        budget = service.budgets.get(budget_id)
        if budget is None:
            raise HTTPException(404)
        return budget

    def budget_page(request: Request, budget: Budget, note: str | None = None) -> HTMLResponse:
        jobs = {s["job_id"]: store.get(s["job_id"]) for s in budget.spends}
        now = datetime.now(UTC).isoformat(timespec="seconds")
        # one ledger in the order things happened (a credit can come before a later spend)
        moves = sorted(
            [("spend", m) for m in budget.spends]
            + [("credit", m) for m in budget.credits]
            + [("refund", m) for m in budget.refunds],
            key=lambda km: km[1].get("at", ""),
        )
        return render(
            request, "budget.html", budget=budget, jobs=jobs, note=note, now=now, moves=moves
        )

    @app.get("/budget/{budget_id}", response_class=HTMLResponse)
    def budget_view(request: Request, budget_id: str) -> HTMLResponse:
        """A budget's page: its balance, every spend, credit and refund. The id lets one see it;
        only the spending key, which the assistant holds, lets one spend it."""
        return budget_page(request, budget_or_404(budget_id))

    @app.get("/budget/{budget_id}/return", response_model=None)
    def budget_return(request: Request, budget_id: str) -> RedirectResponse | HTMLResponse:
        budget = budget_or_404(budget_id)
        problem = settle_budget(budget, pp, service.budgets)
        if problem:
            return budget_page(request, budget_or_404(budget_id), note=problem)
        return RedirectResponse(f"/budget/{budget_id}", status_code=303)

    @app.post("/budget/{budget_id}/check", response_model=None)
    def budget_check(
        request: Request, budget_id: str
    ) -> RedirectResponse | HTMLResponse | JSONResponse:
        if wants_json(request):  # PayPal's buttons on the budget page, after the approval
            problem = settle_budget(budget_or_404(budget_id), pp, service.budgets)
            after = budget_or_404(budget_id)
            return JSONResponse(
                {"status": after.status, "problem": problem, "budget_url": f"/budget/{budget_id}"}
            )
        return budget_return(request, budget_id)

    @app.post("/budget/{budget_id}/limits", response_model=None)
    def budget_limits(
        request: Request, budget_id: str, max_per_job: str = Form(""), stop_now: str = Form("")
    ) -> RedirectResponse | HTMLResponse:
        budget = budget_or_404(budget_id)
        try:
            cap = Decimal(max_per_job.strip().lstrip("$")) if max_per_job.strip() else None
        except InvalidOperation:
            return budget_page(request, budget, note="the limit must be a number of dollars")
        problem = tighten_budget(
            budget.id, service.budgets, max_per_job=cap, stop_now=stop_now == "yes"
        )
        if problem:
            return budget_page(request, budget_or_404(budget_id), note=problem)
        return RedirectResponse(f"/budget/{budget_id}", status_code=303)

    @app.post("/budget/{budget_id}/close", response_model=None)
    def budget_close(
        request: Request, budget_id: str, confirm: str = Form("")
    ) -> RedirectResponse | HTMLResponse:
        if confirm != "yes":
            raise HTTPException(400, "tick the box to confirm")
        problem = close_budget(budget_or_404(budget_id), pp, service.budgets)
        if problem:
            return budget_page(request, budget_or_404(budget_id), note=problem)
        return RedirectResponse(f"/budget/{budget_id}", status_code=303)

    @app.get("/cancel")
    def paypal_cancel(job: str) -> RedirectResponse:
        try:
            store.update(job, expect=("ordered",), status="cancelled")
        except KeyError:
            raise HTTPException(404) from None
        return RedirectResponse(f"/jobs/{job}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        if job.status == "quoted":  # the quote itself, with PayPal's buttons
            kind = "check" if job.kind == "check" else "audit"
            return render(
                request,
                "quote.html",
                job=job,
                quote=quote(job.tokens, words=job.words, books=len(job.books), kind=kind),
                currency=CURRENCY,
                via_agent=request.query_params.get("via") == "assistant",
            )
        share = store.share(job.id) if job.status == "done" else None
        mark = request.query_params.get("mark", "")
        return render(
            request,
            "job.html",
            job=job,
            asked_mark=int(mark) if mark.isdigit() else None,
            asked_note=request.query_params.get("note", "")[:300],
            share_url=f"{base}/r/{share}" if share else None,
            quote=quote(job.tokens, words=job.words, books=len(job.books)),
            **review_context(job),
        )

    def review_context(job: Job) -> dict[str, Any]:
        """What the job page needs for the author's review: contradictions to mark wrong (the
        guarantee) and "needs your judgement" items to rule on."""
        return {
            "contradictions": contradictions_of(job),
            "checks": (
                [j for j in store.all() if j.parent_id == job.id and j.kind == "check"]
                if job.kind == "audit" and job.status == "done"
                else []
            ),
            "judgement": judgement_items(job, store),
            "rulings": RULINGS,
            "guarantee_cap": f"{Decimal(job.quote_usd) * GUARANTEE_CAP:.2f}",
            "guarantee_each": f"{GUARANTEE_USD:.2f}",
        }

    @app.post("/jobs/{job_id}/findings/{index}/rule", response_model=None)
    def finding_rule(
        request: Request, job_id: str, index: int, verdict: str = Form(...), note: str = Form("")
    ) -> RedirectResponse | HTMLResponse | JSONResponse:
        """The author's ruling on an item the model left to them. No money moves."""
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        problem = rule_on_finding(store, job_id, index, verdict, note)
        if wants_json(request):  # the report's triage grid rules in place
            if problem:
                return JSONResponse({"ruled": False, "problem": problem}, status_code=409)
            return JSONResponse({"ruled": True, "verdict": verdict})
        if problem:
            job = store.get(job_id) or job
            return render(
                request,
                "job.html",
                status_code=409,
                job=job,
                quote=quote(job.tokens, words=job.words, books=len(job.books)),
                note=problem,
                **review_context(job),
            )
        return RedirectResponse(f"/jobs/{job_id}#judgement", status_code=303)

    def contradictions_of(job: Job) -> list[dict[str, object]]:
        """A finished job's contradictions with their report index, for the guarantee form."""
        if job.status != "done":
            return []
        p = store.dir(job.id) / "reports" / "report.json"
        try:
            report = AuditReport.model_validate_json(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        marked = {g["finding"]: g for g in job.guarantee}
        return [
            {"index": i, "headline": headline(f.candidate), "marked": marked.get(str(i))}
            for i, f in enumerate(report.findings)
            if f.verdict == "contradiction"
        ]

    @app.post("/jobs/{job_id}/findings/{index}/wrong", response_model=None)
    def finding_wrong(
        request: Request, job_id: str, index: int, note: str = Form("")
    ) -> RedirectResponse | HTMLResponse:
        """The finding guarantee: a contradiction the author marks wrong is noted on the report,
        and part of the price goes back through PayPal (capped at half the price)."""
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        problem, _amount = mark_finding_wrong(job, index, note, pp, store, service.budgets)
        if problem:
            job = store.get(job_id) or job
            return render(
                request,
                "job.html",
                status_code=409,
                job=job,
                quote=quote(job.tokens, words=job.words, books=len(job.books)),
                note=problem,
                **review_context(job),
            )
        return RedirectResponse(f"/jobs/{job_id}#guarantee", status_code=303)

    @app.post("/jobs/{job_id}/check")
    async def make_check(request: Request, job_id: str, draft: UploadFile) -> RedirectResponse:
        """Quote a check of one new draft against this finished audit's bible."""
        require_model()
        _refuse_before_reading([draft])
        data = await draft.read()
        try:
            job, _ = await run_in_threadpool(
                store.create_check, job_id, draft.filename or "draft.txt", data
            )
        except KeyError:
            raise HTTPException(404) from None
        except ValueError as e:
            code = 409 if "finished" in str(e) else 413 if "large" in str(e) else 400
            raise HTTPException(code, str(e)) from None
        return to_quote(request, job.id)

    @app.post("/jobs/{job_id}/delete")
    def delete(job_id: str, confirm: str = Form("")) -> RedirectResponse:
        if confirm != "yes":
            raise HTTPException(400, "tick the box to confirm")
        try:
            delete_job_files(job_id, pp, store)
        except KeyError:
            raise HTTPException(404) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/refund", response_model=None)
    def refund_job(
        request: Request, job_id: str, confirm: str = Form("")
    ) -> RedirectResponse | HTMLResponse:
        if confirm != "yes":
            raise HTTPException(400, "tick the box to confirm")
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        try:
            problem = refund(job, pp, store, service.budgets)
        except (httpx.HTTPError, TimeoutError):
            problem = (
                "PayPal did not answer in time. The refund may already have gone through; "
                "check again in a few minutes — it is never made twice"
            )
        if problem:
            return render(
                request,
                "job.html",
                status_code=409,
                job=store.get(job_id) or job,
                quote=quote(job.tokens, words=job.words, books=len(job.books)),
                note=problem,
            )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    def retry(job_id: str) -> RedirectResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        if job.status == "failed":
            runner.submit(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}/report")
    def report(job_id: str) -> FileResponse:
        if store.get(job_id) is None:
            raise HTTPException(404)
        p = store.dir(job_id) / "reports" / "report.html"
        if not p.exists():
            raise HTTPException(404, "report not ready")
        return FileResponse(p)

    # --- read-only share links: the report and bible without the job's owner key -------------
    SHARE_BACK = "← Preflight: audit your own series"

    def shared_job(share_id: str) -> Job:
        job = store.by_share(share_id)
        if job is None or job.status != "done":
            raise HTTPException(404, "this share link does not exist, or its files were deleted")
        return job

    @app.get("/r/{share_id}")
    def shared(share_id: str) -> RedirectResponse:
        shared_job(share_id)
        return RedirectResponse(f"/r/{share_id}/report", status_code=307)

    @app.get("/r/{share_id}/report", response_class=HTMLResponse)
    def shared_report(share_id: str) -> HTMLResponse:
        job = shared_job(share_id)
        d = store.dir(job.id) / "reports"
        if not (d / "report.json").exists():
            raise HTTPException(404, "report not ready")
        report = AuditReport.model_validate_json((d / "report.json").read_text(encoding="utf-8"))
        html = report_html(
            report,
            back_url="/",
            back_label=SHARE_BACK,
            customer=True,
            bible_url="bible-grid.html" if (d / "bible-grid.html").exists() else None,
            shared=True,
        )
        return HTMLResponse(html)

    @app.get("/r/{share_id}/{name}", response_model=None)
    def shared_bible(share_id: str, name: str) -> HTMLResponse | FileResponse:
        if name not in ("bible-grid.html", "bible.html", "bible.csv"):
            raise HTTPException(404)
        job = shared_job(share_id)
        p = store.dir(job.id) / "reports" / name
        if not p.exists():
            raise HTTPException(404, "bible not ready")
        if name.endswith(".csv"):
            return FileResponse(p, media_type="text/csv")
        html = p.read_text(encoding="utf-8").replace(f"/jobs/{job.id}", "/")
        return HTMLResponse(html.replace("← Back to the job page", SHARE_BACK))

    @app.get("/jobs/{job_id}/download/{name}")
    def download(job_id: str, name: str) -> FileResponse:
        """The finished files as attachments, named after the series: a free host's disk does
        not keep them forever, and the JSON is what other tools (or an assistant) can read."""
        if name not in ("report.html", "report.json", "bible.html", "bible-grid.html", "bible.csv"):
            raise HTTPException(404)
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        p = store.dir(job_id) / "reports" / name
        if not p.exists():
            raise HTTPException(404, "not ready")
        slug = re.sub(r"[^a-z0-9]+", "-", job.series_name.lower()).strip("-") or "series"
        kind = "check" if job.kind == "check" else "audit"
        stem, ext = name.rsplit(".", 1)
        return FileResponse(p, filename=f"{slug}-{kind}-{stem}.{ext}")

    def bible_file(job_id: str, name: str) -> FileResponse:
        if store.get(job_id) is None:
            raise HTTPException(404)
        p = store.dir(job_id) / "reports" / name
        if not p.exists():
            raise HTTPException(404, "bible not ready")
        return FileResponse(p)

    @app.get("/jobs/{job_id}/bible")
    def bible(job_id: str) -> FileResponse:
        return bible_file(job_id, "bible.html")

    # The bible's three views link to each other by relative name, as they do on disk.
    @app.get("/jobs/{job_id}/bible.html")
    def bible_cards(job_id: str) -> FileResponse:
        return bible_file(job_id, "bible.html")

    @app.get("/jobs/{job_id}/bible-grid.html")
    def bible_grid(job_id: str) -> FileResponse:
        return bible_file(job_id, "bible-grid.html")

    @app.get("/jobs/{job_id}/bible.csv")
    def bible_csv(job_id: str) -> FileResponse:
        return bible_file(job_id, "bible.csv")

    if isinstance(pp, FakePayPal):

        def _fake_order(token: str) -> Order:
            try:
                return pp.get_order(token)
            except KeyError:
                raise HTTPException(404, "no such order") from None

        @app.get("/fake-approve", response_class=HTMLResponse)
        def fake_approve(request: Request, token: str) -> HTMLResponse:
            return render(request, "fake_approve.html", order=_fake_order(token))

        @app.post("/fake-approve")
        def fake_approve_post(token: str = Form(...)) -> RedirectResponse:
            o = _fake_order(token)
            pp.approve(token)
            back = str(o.raw.get("return_url") or "/return")
            return RedirectResponse(f"{back}?{urlencode({'token': token})}", status_code=303)

    return app
