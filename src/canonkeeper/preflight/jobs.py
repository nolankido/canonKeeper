"""Job store and background runner.

A job is one series upload. It moves through: quoted → ordered → paid → running → done|failed.
State is a JSON file per job under the workdir so a restart loses nothing; the audit runs in a
daemon thread because the free-tier pacing makes a real series take tens of minutes.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from canonkeeper.config import Settings
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.loaders import Book, Series
from canonkeeper.preflight.pricing import Quote, quote
from canonkeeper.tokens import count_tokens

Status = Literal["quoted", "ordered", "paid", "running", "done", "failed", "cancelled", "deleted"]
DELETABLE: frozenset[str] = frozenset({"quoted", "cancelled", "done", "failed"})


@dataclass
class Job:
    id: str
    series_name: str
    books: list[str]  # filenames in reading order
    tokens: int
    words: int
    quote_usd: str
    status: Status = "quoted"
    order_id: str | None = None
    approve_url: str | None = None
    paid_at: str | None = None
    error: str | None = None
    claims: int | None = None
    contradictions: int | None = None
    kind: str = "audit"  # "audit": a whole series; "check": one new draft against its bible
    parent_id: str | None = None  # for a check: the audit whose bible it is checked against
    draft: str | None = None  # for a check: the draft's file name under draft/
    stage: str | None = None  # "extracting" | "judging" while running
    progress_done: int = 0
    progress_total: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def order_description(job: Job) -> str:
    """What the buyer sees on the PayPal order."""
    if job.kind == "check":
        return f"Preflight draft check: {job.draft} against {job.series_name}"
    return f"Preflight continuity audit: {job.series_name} ({len(job.books)} books)"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _strip_numbering(name: str) -> str:
    """Drop the author's own numbering ("03 - The Return.txt" → "The Return.txt"), but not
    when the number is the whole name ("01.txt" stays "01.txt", not "txt")."""
    p = Path(name)
    stem = re.sub(r"^\d{1,3}[-_. ]+", "", p.stem)
    return (stem or p.stem) + p.suffix


_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_BOOKS = 40
MAX_TOTAL_BYTES = 40 * 10**6  # ~10M tokens; the six Holmes volumes are 2.6 MB
MAX_UNPAID_BYTES = 10**9  # all unpaid quotes together; the oldest are evicted past this


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def dir(self, job_id: str) -> Path:
        # Ids come from secrets.token_urlsafe, and the web routes pass them straight from the
        # URL: anything else (an encoded "..", a slash) must never become a path.
        if not _JOB_ID.fullmatch(job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def _path(self, job_id: str) -> Path:
        return self.dir(job_id) / "job.json"

    def save(self, job: Job) -> None:
        job.updated_at = _now()
        with self._lock:
            self.dir(job.id).mkdir(parents=True, exist_ok=True)
            self._path(job.id).write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")

    def get(self, job_id: str) -> Job | None:
        if not _JOB_ID.fullmatch(job_id):
            return None
        p = self._path(job_id)
        if not p.exists():
            return None
        return Job(**json.loads(p.read_text(encoding="utf-8")))

    def create_check(self, parent_id: str, name: str, data: bytes) -> tuple[Job, Quote]:
        """A check of one new draft against a finished audit's bible. The check gets its own
        copy of the series and the bible, so deleting the original job cannot break it."""
        parent = self.get(parent_id)
        if parent is None:
            raise KeyError(parent_id)
        if parent.status != "done" or parent.kind != "audit":
            raise ValueError("a draft can only be checked against a finished series audit")
        if len(data) > MAX_TOTAL_BYTES:
            raise ValueError(f"draft too large ({len(data) / 1e6:.0f} MB)")
        if b"\x00" in data[:4096]:
            raise ValueError(f"{Path(name).name} is not a text file; upload .txt or .md")
        src = self.dir(parent_id)
        self.make_room(len(data) + self._size(parent_id))
        job_id = secrets.token_urlsafe(8)
        d = self.dir(job_id)
        shutil.copytree(src / "books", d / "books")
        shutil.copy2(src / "bible.jsonl", d / "bible.jsonl")
        stem = _strip_numbering(name) or "draft.txt"
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in stem)
        safe = safe.lstrip(".") or "draft.txt"  # never "..", never a hidden file
        (d / "draft").mkdir(parents=True, exist_ok=True)
        text = data.decode("utf-8", errors="replace")
        (d / "draft" / safe).write_text(text, encoding="utf-8")
        tokens, words = count_tokens(text), len(text.split())
        q = quote(tokens, words=words, books=1, kind="check")
        job = Job(
            id=job_id,
            series_name=parent.series_name,
            books=list(parent.books),
            tokens=tokens,
            words=words,
            quote_usd=q.total_str,
            kind="check",
            parent_id=parent_id,
            draft=safe,
        )
        self.save(job)
        return job, q

    def purge(self, job_id: str) -> Job:
        """Delete everything the customer uploaded or that was derived from it — manuscripts,
        bible, reports, the model-response cache — keeping only job.json as a receipt (id,
        price, order id, timestamps). Not allowed while a payment or a run is in flight."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status == "deleted":
            return job
        if job.status not in DELETABLE:
            raise ValueError(f"cannot delete while the job is {job.status}")
        d = self.dir(job_id)
        with self._lock:
            for child in d.iterdir():
                if child.name == "job.json":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        job.status = "deleted"
        job.series_name = "(deleted)"
        job.books = []
        job.error = None
        self.save(job)
        return job

    def prune_unpaid(self, max_age_hours: float = 24.0) -> int:
        """Delete quotes nobody paid for (status quoted or cancelled, older than the limit),
        uploads included. A public host gives quotes away free; without this every abandoned
        upload stays on disk. Paid jobs are never touched here."""
        cutoff = datetime.now(UTC).timestamp() - max_age_hours * 3600
        removed = 0
        for job in self.all():
            if job.status not in ("quoted", "cancelled"):
                continue
            try:
                updated = datetime.fromisoformat(job.updated_at).timestamp()
            except ValueError:
                continue
            if updated < cutoff:
                with self._lock:
                    shutil.rmtree(self.dir(job.id), ignore_errors=True)
                removed += 1
        return removed

    def _size(self, job_id: str) -> int:
        return sum(f.stat().st_size for f in self.dir(job_id).rglob("*") if f.is_file())

    def make_room(self, needed: int, limit: int | None = None) -> None:
        """Keep unpaid quotes under `limit` bytes on disk by evicting the oldest first. Quotes
        are free and anyone can ask for one, so without a cap a public host's disk is one
        script away from full. Raises ValueError if even an empty store would not fit."""
        limit = MAX_UNPAID_BYTES if limit is None else limit
        if needed > limit:
            raise ValueError("upload too large for this host right now")
        unpaid = [j for j in self.all() if j.status in ("quoted", "cancelled")]
        unpaid.sort(key=lambda j: j.updated_at)  # oldest first
        sizes = {j.id: self._size(j.id) for j in unpaid}
        used = sum(sizes.values())
        for j in unpaid:
            if used + needed <= limit:
                break
            with self._lock:
                shutil.rmtree(self.dir(j.id), ignore_errors=True)
            used -= sizes[j.id]

    def by_order(self, order_id: str) -> Job | None:
        for p in self.root.glob("*/job.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("order_id") == order_id:
                return Job(**data)
        return None

    def all(self) -> list[Job]:
        jobs = [
            Job(**json.loads(p.read_text(encoding="utf-8"))) for p in self.root.glob("*/job.json")
        ]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    # --- creation ---------------------------------------------------------------------------
    def create_from_upload(
        self, series_name: str, files: list[tuple[str, bytes]]
    ) -> tuple[Job, Quote]:
        """`files` are (filename, bytes) in reading order. Text is decoded as UTF-8.
        Raises ValueError on an upload outside the service's limits (the caller turns that
        into a 4xx); the limits are generous for prose and tight enough for a public host."""
        if not files:
            raise ValueError("upload at least one book")
        if len(files) > MAX_BOOKS:
            raise ValueError(f"at most {MAX_BOOKS} books per series")
        total = sum(len(data) for _, data in files)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(
                f"series too large ({total / 1e6:.0f} MB; limit {MAX_TOTAL_BYTES // 10**6} MB)"
            )
        for fname, blob in files:
            if b"\x00" in blob[:4096]:
                raise ValueError(f"{Path(fname).name} is not a text file; upload .txt or .md")
        series_name = series_name.strip()[:200] or "Untitled series"
        self.make_room(total)
        job_id = secrets.token_urlsafe(8)
        d = self.dir(job_id)
        (d / "books").mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        tokens = words = 0
        name: str
        data: bytes
        for i, (name, data) in enumerate(files):
            stem = _strip_numbering(name)
            safe = f"{i + 1:02d}-" + "".join(
                ch if ch.isalnum() or ch in "-_." else "-" for ch in stem
            )
            text = data.decode("utf-8", errors="replace")
            (d / "books" / safe).write_text(text, encoding="utf-8")
            names.append(safe)
            tokens += count_tokens(text)
            words += len(text.split())
        q = quote(tokens, words=words, books=len(files))
        job = Job(
            id=job_id,
            series_name=series_name,
            books=names,
            tokens=tokens,
            words=words,
            quote_usd=q.total_str,
        )
        self.save(job)
        return job, q

    def load_series(self, job: Job) -> Series:
        books: list[Book] = []
        for i, name in enumerate(job.books):
            text = (self.dir(job.id) / "books" / name).read_text(encoding="utf-8")
            title = name.split("-", 1)[1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
            books.append(
                Book(
                    key=name.rsplit(".", 1)[0],
                    title=title or name,
                    index=i,
                    text=text,
                    chapters=chapterize(text),
                )
            )
        return Series(job.series_name, books)


class Runner:
    """Runs paid jobs in background threads. One at a time by default — free-tier pacing is
    per project, so parallel jobs would just contend for the same RPM."""

    def __init__(self, store: JobStore, settings: Settings, *, max_workers: int = 1) -> None:
        self.store = store
        self.settings = settings
        self._sem = threading.Semaphore(max_workers)
        self._threads: dict[str, threading.Thread] = {}

    def resume_interrupted(self, *, stale_after_s: float = 600.0) -> list[str]:
        """Re-queue paid jobs that a restart left behind: paid but never started, or 'running'
        with no progress update for `stale_after_s` (a live job updates every few seconds).
        Responses are cached, so a resumed job picks up where it stopped. Free hosts restart
        and spin down; without this a paid audit could sit at 'running' forever."""
        resumed: list[str] = []
        now = datetime.now(UTC).timestamp()
        for job in self.store.all():
            if job.status == "running":
                try:
                    age = now - datetime.fromisoformat(job.updated_at).timestamp()
                except ValueError:
                    continue
                if age < stale_after_s:
                    continue  # probably still running in another process
                job.status = "paid"
                self.store.save(job)
            if job.status == "paid":
                self.submit(job.id)
                resumed.append(job.id)
        return resumed

    def submit(self, job_id: str) -> None:
        if job_id in self._threads and self._threads[job_id].is_alive():
            return
        t = threading.Thread(
            target=self._run, args=(job_id,), name=f"preflight-{job_id}", daemon=True
        )
        self._threads[job_id] = t
        t.start()

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        t = self._threads.get(job_id)
        if t:
            t.join(timeout)

    def _progress_writer(self, job: Job) -> Callable[[str, int, int], None]:
        """Persist (stage, done, total) on the job so its page and audit_status show it; at
        most every 2 s, plus every stage change and the last step."""
        state = {"at": 0.0}
        stage_seen = {"stage": ""}

        def tick(stage: str, done: int, total: int) -> None:
            now = time.monotonic()
            if stage == stage_seen["stage"] and done < total and now - state["at"] < 2.0:
                return
            state["at"], stage_seen["stage"] = now, stage
            job.stage, job.progress_done, job.progress_total = stage, done, total
            self.store.save(job)

        return tick

    def _run(self, job_id: str) -> None:
        from canonkeeper.llm import make_llm
        from canonkeeper.pipeline import audit, build_bible
        from canonkeeper.report import write_report
        from canonkeeper.report.bible import write_bible

        with self._sem:
            job = self.store.get(job_id)
            if job is None or job.status not in ("paid", "failed"):
                return
            job.status = "running"
            job.error = None
            self.store.save(job)
            try:
                series = self.store.load_series(job)
                d = self.store.dir(job.id)
                settings = self.settings.model_copy(update={"cache_dir": d / "cache"})
                llm = make_llm(settings)
                tick = self._progress_writer(job)
                if job.kind == "check" and job.draft:
                    from canonkeeper.canon import CanonStore
                    from canonkeeper.pipeline import check_manuscript

                    store = CanonStore.load(d / "bible.jsonl")
                    report = check_manuscript(
                        series,
                        store,
                        d / "draft" / job.draft,
                        llm,
                        settings,
                        progress=False,
                        on_progress=tick,
                    )
                else:
                    store = build_bible(
                        series, llm, settings, d / "bible.jsonl", progress=False, on_progress=tick
                    )
                    write_bible(store, series.name, d / "reports" / "bible.html")
                    report = audit(series, store, llm, settings, progress=False, on_progress=tick)
                write_report(report, d / "reports")
                job.claims = report.claims_total
                job.contradictions = len(report.errors)
                job.status = "done"
                job.stage = None
            except Exception as e:  # noqa: BLE001 - the job page shows the failure
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"
                (self.store.dir(job.id) / "error.txt").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
            self.store.save(job)
