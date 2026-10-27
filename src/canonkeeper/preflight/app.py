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

import os
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from canonkeeper.config import Settings, load_settings
from canonkeeper.preflight.jobs import JobStore, Runner
from canonkeeper.preflight.paypal import FakePayPal, PayPal, PayPalClient
from canonkeeper.preflight.pricing import CURRENCY, quote

_TEMPLATES = Path(__file__).parent / "templates"
_SAMPLES = Path(__file__).parent / "samples"


def paypal_env() -> dict[str, str]:
    """PAYPAL_* settings: `.env` in the working directory, overridden by the real environment
    (the same precedence pydantic-settings gives the rest of the configuration)."""
    from dotenv import dotenv_values

    merged: dict[str, str] = {k: v for k, v in dotenv_values(".env").items() if v is not None}
    merged.update(os.environ)
    return merged


def make_paypal(
    settings_env: dict[str, str] | None = None, *, state_dir: Path | None = None
) -> PayPal:
    """`state_dir`: where the fake keeps its orders so `serve` and `mcp` share them."""
    env = settings_env if settings_env is not None else paypal_env()
    mode = env.get("PAYPAL_MODE", "fake" if not env.get("PAYPAL_CLIENT_ID") else "sandbox")
    if mode == "fake":
        return FakePayPal(state_path=state_dir / "fake-paypal.json" if state_dir else None)
    cid, secret = env.get("PAYPAL_CLIENT_ID"), env.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required unless PAYPAL_MODE=fake"
        )
    return PayPalClient(cid, secret, mode=mode)


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
    runner = Runner(store, settings)
    pp = paypal or make_paypal(state_dir=settings.workdir / "preflight")
    base = (
        public_base_url or os.environ.get("PREFLIGHT_PUBLIC_URL") or "http://127.0.0.1:8787"
    ).rstrip("/")

    app = FastAPI(title="Preflight by canonkeeper", docs_url=None, redoc_url=None)
    app.state.store, app.state.runner, app.state.paypal = store, runner, pp
    templates = Jinja2Templates(directory=str(_TEMPLATES))

    def render(request: Request, name: str, **ctx: object) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"paypal_mode": pp.mode, **ctx})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"ok": "true", "paypal": pp.mode, "provider": settings.provider}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        # Job pages are reachable only by their unguessable id; listing them on the home page
        # would show every customer's series to every visitor. Demo mode is single-user.
        jobs = store.all()[:10] if pp.mode == "fake" else []
        return render(request, "index.html", jobs=jobs, sample=_SAMPLES.exists())

    @app.get("/sample", response_class=HTMLResponse)
    def sample() -> FileResponse:
        """A finished audit of the six public-domain Holmes volumes: what a buyer gets."""
        p = _SAMPLES / "sherlock-holmes-report.html"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="text/html")

    @app.post("/quote")
    async def make_quote(
        request: Request, series_name: str = Form(...), files: list[UploadFile] | None = None
    ) -> HTMLResponse:
        files = files or []
        if not files or not any(f.filename for f in files):
            raise HTTPException(400, "upload at least one .txt or .md file")
        payload: list[tuple[str, bytes]] = []
        for f in sorted(files, key=lambda f: f.filename or ""):
            if not f.filename:
                continue
            payload.append((f.filename, await f.read()))
        try:
            job, q = store.create_from_upload(series_name, payload)
        except ValueError as e:
            raise HTTPException(413 if "large" in str(e) else 400, str(e)) from None
        return render(request, "quote.html", job=job, quote=q, currency=CURRENCY)

    @app.post("/jobs/{job_id}/order")
    def order(job_id: str) -> RedirectResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        if job.status not in ("quoted", "cancelled"):
            return RedirectResponse(f"/jobs/{job_id}", status_code=303)
        o = pp.create_order(
            amount=job.quote_usd,
            currency=CURRENCY,
            description=f"Preflight continuity audit: {job.series_name} ({len(job.books)} books)",
            return_url=f"{base}/return",
            cancel_url=f"{base}/cancel?job={job_id}",
            reference=job_id,
        )
        job.order_id, job.approve_url, job.status = o.id, o.approve_url, "ordered"
        store.save(job)
        if not o.approve_url:
            raise HTTPException(502, "PayPal returned no approval link")
        return RedirectResponse(o.approve_url, status_code=303)

    @app.get("/return")
    def paypal_return(token: str) -> RedirectResponse:
        job = store.by_order(token)
        if job is None:
            raise HTTPException(404, "unknown order")
        if job.status == "ordered":
            o = pp.capture_order(token)
            if o.status != "COMPLETED":
                raise HTTPException(402, f"payment not completed: {o.status}")
            from datetime import UTC, datetime

            job.status, job.paid_at = "paid", datetime.now(UTC).isoformat()
            store.save(job)
            runner.submit(job.id)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/cancel")
    def paypal_cancel(job: str) -> RedirectResponse:
        j = store.get(job)
        if j and j.status == "ordered":
            j.status = "cancelled"
            store.save(j)
        return RedirectResponse(f"/jobs/{job}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404)
        return render(
            request,
            "job.html",
            job=job,
            quote=quote(job.tokens, words=job.words, books=len(job.books)),
        )

    @app.post("/jobs/{job_id}/delete")
    def delete(job_id: str, confirm: str = Form("")) -> RedirectResponse:
        if confirm != "yes":
            raise HTTPException(400, "tick the box to confirm")
        try:
            store.purge(job_id)
        except KeyError:
            raise HTTPException(404) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
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
        p = store.dir(job_id) / "reports" / "report.html"
        if not p.exists():
            raise HTTPException(404, "report not ready")
        return FileResponse(p)

    @app.get("/jobs/{job_id}/bible")
    def bible(job_id: str) -> FileResponse:
        p = store.dir(job_id) / "reports" / "bible.html"
        if not p.exists():
            raise HTTPException(404, "bible not ready")
        return FileResponse(p)

    if isinstance(pp, FakePayPal):

        @app.get("/fake-approve", response_class=HTMLResponse)
        def fake_approve(request: Request, token: str, return_to: str) -> HTMLResponse:
            o = pp.get_order(token)
            return render(request, "fake_approve.html", order=o, return_to=return_to)

        @app.post("/fake-approve")
        def fake_approve_post(
            token: str = Form(...), return_to: str = Form(...)
        ) -> RedirectResponse:
            pp.approve(token)
            return RedirectResponse(f"{return_to}?token={token}", status_code=303)

    return app
