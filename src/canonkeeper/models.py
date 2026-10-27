"""Core data model.

The unit of canon is a *Claim*: one atomic fact about one entity, with provenance back to the
exact passage that asserted it. Contradictions are pairs (or small sets) of claims that cannot
both be true, adjudicated with a verdict and an explanation.

Every claim carries a `Provenance`, so every finding in the report can be opened to the line
that caused it. No provenance, no claim.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class Provenance(BaseModel):
    book: str
    """Series-local book key, e.g. `01-a-study-in-scarlet`. Sort order is series order."""
    book_index: int
    book_title: str | None = None
    chapter: str
    chapter_index: int
    quote: str = Field(description="Verbatim passage that supports the claim, <= ~300 chars.")
    char_start: int | None = None
    char_end: int | None = None

    @property
    def locator(self) -> str:
        return f"{self.book_title or self.book} · {self.chapter}"


class ClaimKind(StrEnum):
    ATTRIBUTE = "attribute"  # eye colour, height, scar location, birthplace
    RELATION = "relation"  # sister_of, married_to, employed_by
    STATE = "state"  # alive/dead, location at a point in story time, injured
    EVENT = "event"  # dated or ordered happening
    NAME = "name"  # name / alias / title bound to an entity


class Claim(BaseModel):
    kind: ClaimKind
    subject: str = Field(description="Canonical entity name, e.g. 'John Watson'.")
    predicate: str = Field(description="Snake-case attribute or relation, e.g. 'wound_location'.")
    object: str = Field(description="Value or target entity, normalised, e.g. 'shoulder'.")
    story_time: str | None = Field(
        default=None,
        description="When the claim holds in story time, if stated (e.g. '1881', 'after the "
        "wedding'). Null for timeless attributes.",
    )
    confidence: float = Field(ge=0, le=1, default=0.9)
    provenance: Provenance

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        key = f"{self.subject}|{self.predicate}|{self.object}|{self.provenance.book}|{self.provenance.chapter}|{self.provenance.quote[:80]}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    @property
    def slot(self) -> tuple[str, str]:
        """Entity + predicate. Two claims in the same slot with different objects are a
        candidate conflict."""
        return (self.subject.lower(), self.predicate.lower())


class ExtractionResult(BaseModel):
    """What the extractor returns for one chunk."""

    claims: list[Claim]
    aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Canonical name -> aliases seen in this chunk ('Holmes', 'Mr. Sherlock Holmes').",
    )


Verdict = Literal["contradiction", "deliberate", "ambiguous", "not_conflict"]


class Candidate(BaseModel):
    """A locally detected possible conflict, before the adjudicator sees it."""

    claims: list[Claim]
    reason: str
    heuristic: str

    @property
    def slot(self) -> tuple[str, str]:
        return self.claims[0].slot


class Finding(BaseModel):
    candidate: Candidate
    verdict: Verdict
    severity: Literal["high", "medium", "low"]
    explanation: str
    suggested_fix: str | None = None
    evidence: str | None = None
    """For deliberate / not_conflict: the passage from the series text that justifies clearing
    the conflict, verified to occur in what the adjudicator was shown."""
    adjudicator_model: str

    @property
    def is_error(self) -> bool:
        return self.verdict == "contradiction"


class AuditReport(BaseModel):
    series: str
    books: list[str]
    claims_total: int
    candidates_total: int
    findings: list[Finding]
    models: dict[str, str]
    cost_usd: float | None = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.is_error]
