"""Upload links: how an author's AI assistant gets a whole series into Preflight without pasting
it into a tool call.

A series is hundreds of thousands of words; an assistant's tool call is the wrong pipe for it, and
the books are the person's files anyway. So the assistant asks for a link (`request_upload_link`),
hands it to the person, the person uploads the books on that page and sees the quote, and the
assistant collects the job with `upload_status`. One link makes one job; it lasts a day.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
LIFETIME = timedelta(hours=24)


class UploadLinks:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, token: str) -> Path:
        if not TOKEN.fullmatch(token):
            raise KeyError(token)
        return self.root / f"{token}.json"

    def create(self, series_name: str | None = None) -> str:
        token = secrets.token_urlsafe(18)
        record = {
            "created": datetime.now(UTC).isoformat(),
            "series_name": series_name or "",
            "job_id": None,
        }
        self._path(token).write_text(json.dumps(record), encoding="utf-8")
        return token

    def get(self, token: str) -> dict[str, Any]:
        """The link's record; KeyError when it does not exist or has expired."""
        path = self._path(token)
        try:
            record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(token) from None
        if datetime.now(UTC) - datetime.fromisoformat(record["created"]) > LIFETIME:
            raise KeyError(token)
        return record

    def attach(self, token: str, job_id: str) -> None:
        """Record the job a link made. A link is used once: a second upload is refused."""
        record = self.get(token)
        if record.get("job_id"):
            raise ValueError("this upload link has already been used")
        record["job_id"] = job_id
        tmp = self._path(token).with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(self._path(token))
