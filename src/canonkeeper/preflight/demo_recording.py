"""Real model answers for the demo series, recorded once and shipped in the package.

The demo series (The Saltmarsh Chronicles) is our own invented text, identical for every visitor,
so its model answers are the same every time. Shipping them does two things:

* **Hosted:** a free host that restarts or sleeps loses its disk. `seed()` copies the recording
  into the service's demo cache at start-up, so a cold instance runs the demo with zero requests,
  and a judge can never spend the day's quota just by trying the demo.
* **No key:** a judge who clones the repository and runs `canonkeeper serve` with no key would
  otherwise see the rule-based stand-in's template sentences. `replay()` answers the demo series
  from the recording instead, offline, labelled as recorded answers; anything the recording does
  not cover raises `NotRecorded` and is never sent anywhere.

The recording is made by `scripts/record_demo.py` (real Gemini calls, one model per stage) and is
valid while the prompts are unchanged: `tests/test_demo_recording.py` fails when a prompt change
makes it stale, so it is re-recorded rather than silently half-used.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from canonkeeper.config import Settings, load_settings

RECORDING = Path(__file__).parent / "demo_recording"
MANIFEST = "manifest.json"
# Every setting that changes what is asked of the model (and so the cache key of its answer).
SHAPING = (
    "extract_model",
    "adjudicate_model",
    "extract_mode",
    "extract_hints",
    "extract_hint_passes",
    "extract_reasoning_effort",
    "extract_sweep_reasoning_effort",
    "adjudicate_reasoning_effort",
    "extract_chunk_tokens",
    "adjudicate_context_tokens",
    "adjudicate_batch_size",
    "max_candidates_per_run",
    "temperature",
)


def shaping(settings: Settings) -> dict[str, Any]:
    """The settings a recording must pin so its replay rebuilds exactly the same requests."""
    return {k: getattr(settings, k) for k in SHAPING}


def manifest(root: Path | None = None) -> dict[str, Any] | None:
    """The recording's manifest (models, settings that shape the prompts, date, count), or None
    when no recording is packaged."""
    root = root or RECORDING
    path = root / MANIFEST
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _entries(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*.json") if p.name != MANIFEST)


def seed(cache_dir: Path, root: Path | None = None) -> int:
    """Copy recorded answers the cache does not have yet into it; returns how many were copied.
    Existing entries are never overwritten (a live answer is as good as a recorded one)."""
    root = root or RECORDING
    if manifest(root) is None:
        return 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in _entries(root):
        dst = cache_dir / src.name
        if not dst.exists():
            shutil.copyfile(src, dst)
            copied += 1
    return copied


def replay_settings(base: Settings, root: Path | None = None) -> Settings:
    """Settings that rebuild exactly the requests the recording answered: the recorded provider's
    defaults with the recorded models, efforts and passes, the caller's directories."""
    root = root or RECORDING
    m = manifest(root)
    if m is None:
        raise FileNotFoundError("no demo recording is packaged")
    recorded = load_settings(provider=m["provider"], _env_file=None)
    return recorded.model_copy(
        update={
            **m["settings"],
            "workdir": base.workdir,
            "cache_dir": root,
            "request_log_path": base.request_log,
            "model_fallbacks_enabled": False,
        }
    )


def replay(base: Settings, root: Path | None = None) -> tuple[Settings, Any]:
    """(settings, client) answering only from the recording; a miss raises NotRecorded."""
    root = root or RECORDING
    from canonkeeper.llm.client import OpenAICompatibleLLM

    settings = replay_settings(base, root)
    llm = OpenAICompatibleLLM(
        "recorded-answers",
        "http://127.0.0.1:9/never-called",
        cache_dir=root,
        reasoning_effort={
            settings.extract_model: settings.extract_reasoning_effort,
            settings.adjudicate_model: settings.adjudicate_reasoning_effort,
        },
        offline=True,
    )
    return settings, llm


def label(root: Path | None = None) -> str | None:
    """How a replayed result is described to the person reading it."""
    root = root or RECORDING
    m = manifest(root)
    if m is None:
        return None
    return (
        f"recorded answers from {m['settings']['extract_model']} (reading) and "
        f"{m['settings']['adjudicate_model']} (judging), {m['recorded_on']}, replayed offline"
    )


def record(settings: Settings, dest: Path | None = None, *, workdir: Path) -> dict[str, Any]:
    """Run the demo series through Preflight's own job runner (the audit, then a check of the
    demo draft) with `settings`' provider, and package every answer the runs used into `dest`
    with a manifest. Driving the real runner, not the pipeline directly, is what guarantees a
    replay asks exactly what a visitor's demo job asks (series name, titles, chunking).

    Fallbacks are off: a recording answered by two different models would not replay."""
    dest = dest or RECORDING
    from datetime import UTC, datetime

    from canonkeeper.demo import BOOK1, BOOK2, BOOK3, DRAFT4, SERIES_NAME
    from canonkeeper.preflight.jobs import JobStore, Runner

    run_settings = settings.model_copy(
        update={"workdir": workdir, "model_fallbacks_enabled": False}
    )
    store = JobStore(workdir / "preflight" / "jobs")
    runner = Runner(store, run_settings)
    books = [
        ("01-the-harbour.txt", BOOK1.encode()),
        ("02-the-wreck.txt", BOOK2.encode()),
        ("03-the-return.txt", BOOK3.encode()),
    ]
    audit, _ = store.create_from_upload(SERIES_NAME, books)
    results: dict[str, Any] = {}
    for job in (audit, None):
        if job is None:  # the draft check, once the audit is done
            job, _ = store.create_check(audit.id, "draft-04-the-ledger.txt", DRAFT4.encode())
        if not job.demo:
            raise RuntimeError("the recording's upload was not recognised as the demo series")
        store.update(job.id, status="paid")
        runner.submit(job.id)
        runner.wait(job.id)
        done = store.get(job.id)
        if done is None or done.status != "done":
            why = f"{done.status}: {done.error}" if done else "missing"
            raise RuntimeError(f"recording stopped: the {job.kind} job is {why}")
        results[job.kind] = {"claims": done.claims, "contradictions": done.contradictions}

    dest.mkdir(parents=True, exist_ok=True)
    for old in _entries(dest):
        old.unlink()
    entries = _entries(runner.demo_cache_dir)
    for src in entries:
        shutil.copyfile(src, dest / src.name)
    meta = {
        "provider": settings.provider,
        "settings": shaping(run_settings),
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "entries": len(entries),
        "results": results,
    }
    (dest / MANIFEST).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta
