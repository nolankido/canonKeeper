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


def quote(tokens: int, *, words: int = 0, books: int = 0) -> Quote:
    usage = (PER_100K_TOKENS_USD * Decimal(tokens) / Decimal(100_000)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = max(MIN_USD, BASE_USD + usage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Quote(tokens, words, books, BASE_USD, usage, total)
