"""Quoting. The price is a function of the token count, which is known before any model runs.

Published, deterministic, and explained on the quote page: authors should be able to predict
the bill from their word count. The numbers are a starting point, not a business plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

BASE_USD = Decimal("5.00")
PER_100K_TOKENS_USD = Decimal("4.00")
MIN_USD = Decimal("5.00")
# A check reads one new book against a bible that already exists, so it starts lower.
CHECK_BASE_USD = Decimal("2.00")
CHECK_MIN_USD = Decimal("2.00")
CURRENCY = "USD"


@dataclass(frozen=True)
class Quote:
    tokens: int
    words: int
    books: int
    base_usd: Decimal
    usage_usd: Decimal
    total_usd: Decimal

    @property
    def total_str(self) -> str:
        return f"{self.total_usd:.2f}"

    @property
    def explanation(self) -> str:
        return (
            f"${self.base_usd:.2f} base + ${PER_100K_TOKENS_USD:.2f} per 100k tokens × "
            f"{self.tokens / 100_000:.2f} = ${self.total_usd:.2f}"
        )


def quote(tokens: int, *, words: int = 0, books: int = 0, kind: str = "audit") -> Quote:
    """`kind="check"`: a new draft checked against an existing bible; `tokens` is the draft's."""
    base, floor = (CHECK_BASE_USD, CHECK_MIN_USD) if kind == "check" else (BASE_USD, MIN_USD)
    usage = (PER_100K_TOKENS_USD * Decimal(tokens) / Decimal(100_000)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = max(floor, base + usage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Quote(tokens, words, books, base, usage, total)
