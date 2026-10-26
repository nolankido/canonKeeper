"""Probe which Gemini models are actually serving right now, and how fast.

    python scripts/probe_models.py [model ...]

For each model: one tiny JSON call, then one realistic extraction call on a ~6k-token chunk of
A Study in Scarlet (from the Gutenberg cache if present). Prints latency / status / token
counts and writes probe.jsonl. Free-tier capacity varies by hour; run this before a long build
and set CANONKEEPER_EXTRACT_MODEL to whatever answers quickly.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

from canonkeeper.config import load_settings
from canonkeeper.extract import prompts
from canonkeeper.llm.client import OpenAICompatibleLLM

CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]


def main() -> None:
    settings = load_settings()
    models = sys.argv[1:] or CANDIDATES
    chunk_path = settings.cache_dir / "gutenberg" / "pg244.txt"
    chunk = (
        chunk_path.read_text(encoding="utf-8")[20_000:44_000]
        if chunk_path.exists()
        else "x " * 6000
    )
    extraction = [
        {"role": "system", "content": prompts.SYSTEM},
        {
            "role": "user",
            "content": prompts.USER_TEMPLATE.format(
                series="Sherlock Holmes",
                book_index=1,
                book_title="A Study in Scarlet",
                chapter_index=1,
                chapter_title="probe",
                chunk_note="",
                text=chunk,
            ),
        },
    ]
    out = Path("probe.jsonl")
    for model in models:
        llm = OpenAICompatibleLLM(
            settings.api_key or "",
            settings.base_url,
            cache_dir=None,
            timeout_s=120,
            requests_per_minute=0,
            max_retries=0,
            reasoning_effort={model: settings.extract_reasoning_effort},
            request_log=out,
        )
        for label, msgs in (
            ("tiny", [{"role": "user", "content": 'Reply with JSON {"ok": true}'}]),
            ("extract-6k", extraction),
        ):
            t0 = time.monotonic()
            try:
                text = llm.complete(model, msgs, json_mode=True)
                dt = time.monotonic() - t0
                n = 0
                with contextlib.suppress(Exception):
                    n = len(json.loads(text).get("claims", []))
                print(
                    f"{model:24s} {label:11s} ok   {dt:6.1f}s  out={len(text):5d} chars  claims={n}  "
                    f"tokens={llm.usage.prompt_tokens}/{llm.usage.completion_tokens}",
                    flush=True,
                )
            except Exception as e:  # noqa: BLE001
                dt = time.monotonic() - t0
                msg = str(e).replace("\n", " ")[:160]
                print(
                    f"{model:24s} {label:11s} FAIL {dt:6.1f}s  {type(e).__name__}: {msg}",
                    flush=True,
                )
                break


if __name__ == "__main__":
    main()
