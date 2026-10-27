"""Upload links: how an author's AI assistant gets a whole series into Preflight without pasting
it into a tool call.

A series is hundreds of thousands of words; an assistant's tool call is the wrong pipe for it, and
the books are the person's files anyway. So the assistant asks for a link (`request_upload_link`),
hands it to the person, the person uploads the books on that page and sees the quote, and the
assistant collects the job with `upload_status`. One link makes one job; it lasts a day.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from canonkeeper.preflight.filelock import file_lock, read_json, write_json

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
        write_json(self._path(token), record, indent=None)
        return token

    def get(self, token: str) -> dict[str, Any]:
        """The link's record; KeyError when it does not exist or has expired."""
        path = self._path(token)
        try:
            record = read_json(path)
        except (OSError, ValueError):
            record = None
        if record is None:
            raise KeyError(token)
        if datetime.now(UTC) - datetime.fromisoformat(record["created"]) > LIFETIME:
            raise KeyError(token)
        return record

    def attach(self, token: str, job_id: str) -> None:
        """Record the job a link made. A link is used once: a second upload is refused."""
        with file_lock(self._path(token).with_suffix(".lock")):
            record = self.get(token)
            if record.get("job_id"):
                raise ValueError("this upload link has already been used")
            record["job_id"] = job_id
            write_json(self._path(token), record, indent=None)
