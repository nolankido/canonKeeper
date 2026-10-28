"""Show each finding with the passage that says it.

The bible often holds the same fact several times in one book: "I had a Jezail bullet through it
some time before" and "sat nursing my wounded leg" are both John Watson, wound, leg, in *The
Sign of the Four*. The screen picks one of them, and the first is not always the one a reader
needs: the leg is in the second. Before a report is written, each claim in a finding is swapped
for the copy of the *same* fact (same character, same fact, same value, same book) whose
sentence names the value, or, for a role two people share ("the landlady"), names the person.
Every copy is grounded to a verbatim quote, so this changes which true sentence is shown, never
what is claimed. Judging is untouched: it runs before this and on the screen's own picks.
"""

from __future__ import annotations

import re

from canonkeeper.canon import CanonStore
from canonkeeper.models import AuditReport, Claim

_TITLES = {"mr", "mrs", "miss", "ms", "dr", "doctor", "sir", "lady", "lord", "captain", "the"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]+", text.lower()) if len(w) > 2}


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", "", value.lower())).strip()


def _names(claim: Claim, *, role: bool) -> bool:
    """Does the sentence say the thing the finding turns on: the person, for a shared role; the
    value, otherwise (a yes/no value is never in the sentence, so it never asks for a swap)."""
    quote = _words(claim.provenance.quote)
    if role:
        return bool((_words(claim.subject) - _TITLES) & quote)
    return bool(_words(claim.object) & quote)


def _book(c: Claim) -> str:
    return c.provenance.book


def telling_passages(report: AuditReport, store: CanonStore) -> int:
    """Swap each finding's claims for the most telling copy of the same fact; returns how many
    were swapped."""
    slots = store.slots()
    swapped = 0
    for finding in report.findings:
        role = finding.candidate.heuristic == "role_collision"
        claims = finding.candidate.claims
        for i, c in enumerate(claims):
            copies = [
                o
                for o in slots.get((c.subject.lower(), c.predicate), [])
                if _book(o) == _book(c) and _norm(o.object) == _norm(c.object)
            ]
            if c.object.lower() in ("true", "false") or _names(c, role=role):
                continue
            better = [o for o in copies if _names(o, role=role)]
            if better:
                claims[i] = better[0]
                swapped += 1
    return swapped
