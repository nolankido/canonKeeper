"""Will today's model quota cover this audit? Asked before a PayPal order is created.

On a free tier the binding limit is requests per model per day, not money. A buyer who pays for an
audit the day's quota cannot finish gets a failed job and a refund, which is honest but a poor way to
spend a judge's five minutes. So the order is refused up front, with the time the quota resets.

Extraction is counted exactly, cached chunks excluded (`Extractor.plan`, as `build --dry-run` does).
Judging cannot be counted before the bible exists (it depends on the candidates found), so a small
reserve on the judging model is required instead: enough for a typical series, and a run that needs
more stops at the client-side guard and resumes for free. The demo series, once its answers are all
cached, needs no reserve at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from canonkeeper.config import Settings
from canonkeeper.corpus import Series

JUDGE_RESERVE = 5  # requests: the six Holmes volumes were judged in 5 batches


@dataclass
class QuotaCheck:
    ok: bool
    reason: str | None = None
    needed: dict[str, int] = field(default_factory=dict)
    left: dict[str, int] = field(default_factory=dict)


def _left_today(settings: Settings, model: str, used: dict[str, int]) -> int | None:
    """Requests left today on `model` and the models it falls back to; None = no daily cap."""
    chain = [model, *settings.model_fallbacks.get(model, [])]
    caps = [(m, settings.daily_budget.get(m)) for m in chain]
    if all(cap is None for _, cap in caps):
        return None
    return sum(
        max(0, cap - settings.budget_margin - used.get(m, 0)) for m, cap in caps if cap is not None
    )


def check_quota(settings: Settings, series: Series, cache_dir: Path, *, demo: bool) -> QuotaCheck:
    from canonkeeper.extract.extractor import Extractor
    from canonkeeper.llm import make_llm
    from canonkeeper.llm.client import count_requests_today

    if not settings.daily_budget:
        return QuotaCheck(ok=True)  # no daily caps for this provider (mock, local, paid)
    llm = make_llm(settings.model_copy(update={"cache_dir": cache_dir}))
    total, cached = Extractor(llm, settings).plan(series)
    to_extract = total - cached
    to_judge = 0 if (demo and to_extract == 0) else JUDGE_RESERVE
    used = count_requests_today(settings.request_log, settings.day_reset_utc_offset)
    check = QuotaCheck(ok=True)
    for model, need in (
        (settings.extract_model, to_extract),
        (settings.adjudicate_model, to_judge),
    ):
        left = _left_today(settings, model, used)
        if left is None or need == 0:
            continue
        check.needed[model] = check.needed.get(model, 0) + need
        check.left[model] = left
        if check.needed[model] > left:
            check.ok = False
    if not check.ok:
        short = ", ".join(
            f"{m} needs {check.needed[m]} requests, {check.left[m]} left today"
            for m in check.needed
            if check.needed[m] > check.left[m]
        )
        check.reason = (
            "Today's free model quota cannot finish this audit, so it has not been ordered and "
            f"nothing was charged ({short}). The quota resets at midnight US Pacific time; "
            "come back then, or try the demo series."
        )
    return check
