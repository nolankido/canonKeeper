"""Adjudication prompts (Muse Wonder).

The adjudicator sees: the candidate pair with quotes, every other claim the store holds about
the same entity, and — when the budget allows — the full text of the chapters involved, or the
whole series. The 1M-token context is what makes "read both books and decide" feasible.
"""

from canonkeeper.models import Claim

SYSTEM = """You are the senior continuity editor for a multi-book fiction series. You are shown
two or more facts the series bible recorded about the same entity that appear to disagree, with
their verbatim source passages, plus surrounding context. Decide what is going on.

Verdicts
- "contradiction": the passages cannot both be true as written; a reader would call it an error.
- "deliberate":    the difference is explained by the story — a reveal, a lie by a character, a
                   disguise, an unreliable narrator, a change over time that the text supports.
- "ambiguous":     the passages differ but the text is vague enough that either could be read
                   as consistent (e.g. "wounded in the leg" where the shoulder was hit earlier
                   and a second injury is plausible but unstated).
- "not_conflict":  the bible extraction was wrong — the facts are about different entities,
                   the predicate was mislabelled, or the values are actually the same thing.

Severity (only meaningful for "contradiction")
- high:   a fact readers track — deaths, family relations, names, origin, major injuries.
- medium: physical description, dates off by a little, geography.
- low:    minor props, phrasing-level inconsistencies.

Be concrete. Quote the exact words that create the conflict. If "contradiction", propose the
smallest edit to the LATER passage that fixes it (fix later books, never earlier ones — earlier
books are already published).

Return ONLY JSON:
{"verdict": "...", "severity": "high|medium|low", "explanation": "...", "suggested_fix": "... or null"}
"""

USER_TEMPLATE = """Series: {series}

## Candidate conflict ({heuristic})
{reason}

{claims_block}

## Everything else the bible knows about {entity}
{entity_block}

## Context passages
{context_block}

Decide. JSON only."""


def claim_line(i: int, c: Claim) -> str:
    when = f" (story time: {c.story_time})" if c.story_time else ""
    return (
        f"[{i}] {c.subject} · {c.predicate} = {c.object}{when}\n"
        f"    source: Book {c.provenance.book_index + 1} “{c.provenance.book}”, {c.provenance.chapter}\n"
        f"    quote: “{c.provenance.quote}”"
    )
