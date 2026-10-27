"""Record real model answers for the demo series and package them (T4).

    python scripts/record_demo.py            # uses GEMINI_API_KEY from the environment or .env

Runs The Saltmarsh Chronicles through Preflight's own job runner (the audit, then a check of the
demo draft) on the configured provider with fallbacks off, then writes every answer used, plus a
manifest, to src/canonkeeper/preflight/demo_recording/. About a dozen Flash-Lite requests and two
or three judging requests; run it when the day's quota has room (after the 21:00 HST reset).
Commit the directory; `tests/test_demo_recording.py` checks it still matches the prompts.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from canonkeeper.config import load_settings
from canonkeeper.preflight import demo_recording


def main() -> int:
    settings = load_settings(model_fallbacks_enabled=False)
    if settings.provider == "mock" or not settings.can_run_models:
        print("needs a real provider and key (GEMINI_API_KEY); nothing was recorded")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        # Count against the main quota log so `canonkeeper quota` sees these requests.
        settings = settings.model_copy(update={"request_log_path": settings.request_log.resolve()})
        meta = demo_recording.record(settings, workdir=Path(tmp))
    print(json.dumps(meta, indent=2))
    print(f"wrote {meta['entries']} answers to {demo_recording.RECORDING}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
