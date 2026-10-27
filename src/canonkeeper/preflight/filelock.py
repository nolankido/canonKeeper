"""A small cross-process lock: `serve` and `canonkeeper mcp` share one jobs folder, so a
status change made in one process must not interleave with another's.

An exclusive-create lock file (works the same on Windows and Linux, no dependency), held only
for the few milliseconds a read-modify-write takes. A lock file older than `stale_s` is left
over from a crashed process and is taken over.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

_local: dict[str, threading.RLock] = {}
_guard = threading.Lock()


def _thread_lock(key: str) -> threading.RLock:
    with _guard:
        return _local.setdefault(key, threading.RLock())


def _stale(path: Path, stale_s: float) -> bool:
    """Left behind: older than `stale_s` (locks are held for milliseconds), or its holder's
    process is gone (checked where the OS lets us ask without side effects)."""
    if time.time() - path.stat().st_mtime > stale_s:
        return True
    if os.name != "posix":
        return False
    pid = int(path.read_text() or "0")
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


_depth: dict[str, int] = {}  # per lock file; only touched by the thread holding its RLock


@contextlib.contextmanager
def file_lock(path: Path, *, timeout_s: float = 45.0, stale_s: float = 30.0) -> Iterator[None]:
    """Hold `path` (a lock file) exclusively across threads and processes. Re-entrant within a
    thread: only the outermost holder creates and removes the file."""
    key = str(path)
    with _thread_lock(key):
        if _depth.get(key):
            _depth[key] += 1
            try:
                yield
            finally:
                _depth[key] -= 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except (FileExistsError, PermissionError):  # Windows: a lock being deleted
                with contextlib.suppress(OSError, ValueError):
                    if _stale(path, stale_s):
                        path.unlink()
                        continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"could not lock {path}") from None
                time.sleep(0.02)
        _depth[key] = 1
        try:
            yield
        finally:
            _depth[key] = 0
            with contextlib.suppress(OSError):
                path.unlink()
