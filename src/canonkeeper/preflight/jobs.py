"""Job store and background runner.

A job is one series upload. It moves through: quoted → ordered → paid → running → done|failed.
State is a JSON file per job under the workdir so a restart loses nothing; the audit runs in a
daemon thread because the free-tier pacing makes a real series take tens of minutes.
"""

from __future__ import annotations

import contextlib
import re
import secrets
import shutil
import threading
import time
import traceback
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from canonkeeper.config import Settings
from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.loaders import Book, Series
from canonkeeper.corpus.loaders import decode_text as decode_upload
from canonkeeper.preflight.filelock import file_lock, read_json, write_json
from canonkeeper.preflight.pricing import Quote, quote
from canonkeeper.tokens import count_tokens

Status = Literal[
    "quoted",
    "ordered",
    "paid",
    "running",
    "done",
    "failed",
    "cancelled",
    "held",
    "refunding",
    "refunded",
    "deleted",
]
DELETABLE: frozenset[str] = frozenset({"quoted", "cancelled", "done", "failed", "refunded"})


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
    capture_id: str | None = None  # PayPal's capture, needed to refund it
    earlier_orders: list[str] = field(default_factory=list)  # replaced by a re-order
    hold_reason: str | None = None  # why PayPal is holding a captured payment
    # the one order being captured for this job (so two approved orders never both get
    # captured), and since when
    capturing: str | None = None
    capturing_at: str | None = None
    refund_id: str | None = None
    refunded_at: str | None = None
    refund_status: str | None = None  # PayPal's: COMPLETED, or PENDING while it clears
    error: str | None = None
    claims: int | None = None
    contradictions: int | None = None
    kind: str = "audit"  # "audit": a whole series; "check": one new draft against its bible
    parent_id: str | None = None  # for a check: the audit whose bible it is checked against
    draft: str | None = None  # for a check: the draft's file name under draft/
    demo: bool = False  # the invented demo series: its model responses are shared
    budget_id: str | None = None  # paid from an agent budget instead of its own PayPal order
    # The finding guarantee: contradictions the author marked wrong, and what was given back.
    guarantee: list[dict[str, str]] = field(default_factory=list)
    # The author's rulings on "needs your judgement" items: finding, verdict, note, at.
    rulings: list[dict[str, str]] = field(default_factory=list)
    stage: str | None = None  # "extracting" | "judging" while running
    progress_done: int = 0
    progress_total: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class _Stopped(Exception):
    """Raised inside a run when its job stopped being "running" (refunded, deleted)."""


def _is_demo(texts: list[str]) -> bool:
    """True when the upload is exactly the invented Saltmarsh series, in order."""
    from canonkeeper.demo import BOOK1, BOOK2, BOOK3

    return [t.replace("\r\n", "\n") for t in texts] == [BOOK1, BOOK2, BOOK3]


def _is_demo_draft(text: str) -> bool:
    """True when the draft is exactly the demo series' own unpublished fourth book."""
    from canonkeeper.demo import DRAFT4

    return text.replace("\r\n", "\n") == DRAFT4


_read_json = read_json  # the job store's name for it


def _unchanged_since(seen: str) -> Callable[[Job], bool]:
    return lambda j: j.updated_at == seen


def order_description(job: Job) -> str:
    """What the buyer sees on the PayPal order."""
    if job.kind == "check":
        return f"Preflight draft check: {job.draft} against {job.series_name}"
    return f"Preflight continuity audit: {job.series_name} ({len(job.books)} books)"


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SMALL = {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to"}


def _title_from_filename(stem: str) -> str:
    """ "the-harbour" → "The Harbour"; a title the author already cased is left alone."""
    title = stem.replace("-", " ").replace("_", " ").strip()
    if not title.islower():
        return title
    words = title.split()
    return " ".join(w if i and w in _SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words))


def draft_title(filename: str) -> str:
    """ "draft-04-the-ledger.txt" → "The Ledger (draft)": how the report names the new book."""
    stem = re.sub(r"^(?:draft[-_ ]*)?\d{0,3}[-_. ]*", "", Path(filename).stem, flags=re.I)
    return f"{_title_from_filename(stem)} (draft)" if stem else "New draft"


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
            write_json(self._path(job.id), asdict(job), indent=2)

    def lock(self, job_id: str) -> contextlib.AbstractContextManager[None]:
        """Exclusive across threads and processes (serve and mcp share this folder)."""
        if not _JOB_ID.fullmatch(job_id):
            raise KeyError(job_id)
        return file_lock(self.root / ".locks" / f"{job_id}.lock")

    def update(
        self,
        job_id: str,
        *,
        expect: Collection[str] | None = None,
        where: Callable[[Job], bool] | None = None,
        **fields: Any,
    ) -> Job | None:
        """Atomically change a job, only if its *stored* status is in `expect` (and `where`
        holds). Returns the updated job, or None if it did not qualify. Every status change
        goes through here: a decision made on a copy read earlier can be stale by the time
        it is written (a refund racing a retry, a cancel racing a capture)."""
        with self.lock(job_id):
            job = self.get(job_id)
            if job is None or (expect is not None and job.status not in expect):
                return None
            if where is not None and not where(job):
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            self.save(job)
            return job

    def get(self, job_id: str) -> Job | None:
        if not _JOB_ID.fullmatch(job_id):
            return None
        data = _read_json(self._path(job_id))
        return Job(**data) if data is not None else None

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
        draft_text = decode_upload(data, name)
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
        text = draft_text
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
            demo=parent.demo and _is_demo_draft(draft_text),
        )
        self.save(job)
        return job, q

    def purge(self, job_id: str) -> Job:
        """Delete everything the customer uploaded or that was derived from it — manuscripts,
        bible, reports, the model-response cache — keeping only job.json as a receipt (id,
        price, order id, timestamps). Not allowed while a payment or a run is in flight."""
        with self.lock(job_id):
            return self._purge_locked(job_id)

    def _purge_locked(self, job_id: str) -> Job:
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
            data = _read_json(p)
            if data and (
                data.get("order_id") == order_id or order_id in data.get("earlier_orders", [])
            ):
                return Job(**data)
        return None

    def all(self) -> list[Job]:
        jobs = [Job(**d) for p in self.root.glob("*/job.json") if (d := _read_json(p))]
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
        texts = [decode_upload(blob, fname) for fname, blob in files]
        series_name = series_name.strip()[:200] or "Untitled series"
        self.make_room(total)
        job_id = secrets.token_urlsafe(8)
        d = self.dir(job_id)
        (d / "books").mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        tokens = words = 0
        for i, name in enumerate(fname for fname, _ in files):
            stem = _strip_numbering(name)
            safe = f"{i + 1:02d}-" + "".join(
                ch if ch.isalnum() or ch in "-_." else "-" for ch in stem
            )
            text = texts[i]
            (d / "books" / safe).write_text(text, encoding="utf-8")
            names.append(safe)
            tokens += count_tokens(text)
            words += len(text.split())
        q = quote(tokens, words=words, books=len(files))
        job = Job(
            id=job_id,
            series_name=series_name,
            demo=_is_demo(texts),
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
            title = _title_from_filename(name.split("-", 1)[1].rsplit(".", 1)[0])
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
        self._submit_lock = threading.Lock()
        self.keepalive_url: str | None = None  # pinged while jobs run (see create_app)
        self.keepalive_every = 240.0
        # Also pinged, busy or not, until this moment (PREFLIGHT_KEEP_AWAKE_UNTIL): a free host
        # that sleeps wipes its disk, and judges must find the demo and their jobs where they
        # left them. One instance awake all month is within Render's 750 free hours.
        self.keep_awake_until: datetime | None = None
        self._keepalive: threading.Thread | None = None

    def busy(self) -> bool:
        return any(t.is_alive() for t in list(self._threads.values()))

    def _awake_wanted(self) -> bool:
        until = self.keep_awake_until
        return self.busy() or (until is not None and datetime.now(UTC) < until)

    def _keep_alive(self) -> None:
        import httpx

        while True:
            time.sleep(self.keepalive_every)
            if not self._awake_wanted():
                return
            with contextlib.suppress(httpx.HTTPError):  # a missed ping fails nothing
                httpx.get(self.keepalive_url or "", timeout=10)

    def _replays(self, job: Job) -> str | None:
        """With no model key, the demo series is answered from the packaged Gemini recording
        (a label saying so), not by the rule-based stand-in: a judge without a key still sees
        real model output. Only the exact demo books, and the exact demo draft for a check."""
        if self.settings.provider != "mock" or not job.demo:
            return None
        from canonkeeper.preflight import demo_recording

        return demo_recording.label()

    @property
    def demo_cache_dir(self) -> Path:
        """The demo series is our own invented text, identical for every visitor, so its
        responses are shared: one run warms them, later ones cost no quota."""
        return self.store.root.parent / "demo-cache"

    def cache_dir_for(self, job: Job) -> Path:
        return self.demo_cache_dir if job.demo else self.store.dir(job.id) / "cache"

    def ensure_keepalive(self) -> None:
        """Start the pinger if there is a URL and a reason to stay awake, and it is not running."""
        if not self.keepalive_url or not self._awake_wanted():
            return
        if not (self._keepalive and self._keepalive.is_alive()):
            self._keepalive = threading.Thread(
                target=self._keep_alive, name="preflight-keepalive", daemon=True
            )
            self._keepalive.start()

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
                stale = self.store.update(
                    job.id,
                    expect=("running",),
                    where=_unchanged_since(job.updated_at),  # no heartbeat since we looked
                    status="paid",
                )
                if stale is None:
                    continue
                job = stale
            if job.status == "paid":
                self.submit(job.id)
                resumed.append(job.id)
        return resumed

    def submit(self, job_id: str) -> None:
        with self._submit_lock:  # two confirmations at once must not start two runs
            if job_id in self._threads and self._threads[job_id].is_alive():
                return
            t = threading.Thread(
                target=self._run, args=(job_id,), name=f"preflight-{job_id}", daemon=True
            )
            self._threads[job_id] = t
            t.start()
            self.ensure_keepalive()

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        t = self._threads.get(job_id)
        if t:
            t.join(timeout)

    heartbeat_s = 60.0

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        """Touch a running job every minute, so a single long model call (a big adjudication
        batch, a Retry-After wait) never makes it look abandoned to resume_interrupted in this
        or another process."""
        while not stop.wait(self.heartbeat_s):
            try:
                if self.store.update(job_id, expect=("running",)) is None:
                    return
            except (OSError, TimeoutError):
                continue  # a missed beat is fine; the next one counts

    def watch(
        self, every_s: float = 300.0, *, on_held: Callable[[Job], object] | None = None
    ) -> threading.Event:
        """Every few minutes: re-run resume_interrupted (at startup a job whose process just
        died still looks alive, since its last heartbeat is recent), and hand each "held" job
        to `on_held`, which asks PayPal whether the payment has cleared. Returns an event that
        stops the watcher."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(every_s):
                with contextlib.suppress(Exception):
                    self.resume_interrupted()
                for job in self.store.all() if on_held else []:
                    if job.status == "held" and on_held:
                        with contextlib.suppress(Exception):
                            on_held(job)

        threading.Thread(target=loop, name="preflight-resume", daemon=True).start()
        return stop

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
            moved = self.store.update(
                job.id,
                expect=("running",),
                stage=stage,
                progress_done=done,
                progress_total=total,
            )
            if moved is None:  # refunded, deleted or taken over meanwhile: stop working
                raise _Stopped(job.id)

        return tick

    def _run(self, job_id: str) -> None:
        from canonkeeper.llm import make_llm
        from canonkeeper.pipeline import audit, build_bible
        from canonkeeper.report import write_report
        from canonkeeper.report.bible import write_bible

        with self._sem:
            job = self.store.update(job_id, expect=("paid", "failed"), status="running", error=None)
            if job is None:
                return
            beat = threading.Event()
            threading.Thread(
                target=self._heartbeat, args=(job_id, beat), name=f"beat-{job_id}", daemon=True
            ).start()
            try:
                series = self.store.load_series(job)
                d = self.store.dir(job.id)
                cache = self.cache_dir_for(job)
                settings = self.settings.model_copy(update={"cache_dir": cache})
                replayed = self._replays(job)
                if replayed:
                    from canonkeeper.preflight import demo_recording

                    settings, llm = demo_recording.replay(settings)
                else:
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
                        title=draft_title(job.draft),
                    )
                else:
                    store = build_bible(
                        series, llm, settings, d / "bible.jsonl", progress=False, on_progress=tick
                    )
                    write_bible(
                        store, series.name, d / "reports" / "bible.html", back_url=f"/jobs/{job.id}"
                    )
                    report = audit(series, store, llm, settings, progress=False, on_progress=tick)
                if replayed:
                    report.models["replayed"] = replayed
                write_report(report, d / "reports", back_url=f"/jobs/{job.id}", customer=True)
                self.store.update(
                    job.id,
                    expect=("running",),
                    claims=report.claims_total,
                    contradictions=len(report.errors),
                    status="done",
                    stage=None,
                )
            except _Stopped:
                pass  # the job's status was changed under us; it is no longer ours to write
            except Exception as e:  # noqa: BLE001 - the job page shows the failure
                try:
                    if self.store.update(
                        job.id,
                        expect=("running",),
                        status="failed",
                        error=f"{type(e).__name__}: {e}",
                    ):
                        (self.store.dir(job.id) / "error.txt").write_text(
                            traceback.format_exc(), encoding="utf-8"
                        )
                except Exception:  # noqa: BLE001 - the periodic resume check recovers it
                    pass
            finally:
                beat.set()
