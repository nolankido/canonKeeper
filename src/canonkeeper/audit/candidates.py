"""Local, zero-cost candidate detection.

The adjudicator (Muse Wonder) is expensive and we want it to spend its attention on things
that might actually be wrong. These heuristics over-generate on purpose; precision is the
adjudicator's job, recall is ours.

Heuristics
- slot_mismatch:   same entity + predicate, different normalised value, in a *single-valued*
                   predicate (eye_color, birthplace, wound_location, ...).
- dead_speaks:     entity marked dead at book i, then has a state/event claim in book > i with
                   no `alive` claim in between (resurrection without a reveal).
- name_drift:      first_name / surname claims that differ for one entity.
- timeline:        numeric story_time values that go backwards for `born`/`died`/`age`.
"""

from __future__ import annotations

import re
from itertools import combinations

from rapidfuzz import fuzz

from canonkeeper.canon import CanonStore
from canonkeeper.models import Candidate, Claim, ClaimKind

SINGLE_VALUED = {
    "eye_color",
    "hair_color",
    "birthplace",
    "birth_year",
    "nationality",
    "wound_location",
    "handedness",
    "height",
    "mother",
    "father",
    "child_of",
}
MULTI_VALUED_OK = {
    "alias",
    "nickname",
    "friend_of",
    "colleague_of",
    "location",
    "residence",
    "occupation",
    "possession",
    "habit",
    "skill",
    "illness",
    "education",
    "member_of",
}

_NUM = re.compile(r"\b(1[5-9]\d\d|20\d\d|\d{1,3})\b")


def _norm(v: str) -> str:
    v = v.strip().lower()
    v = re.sub(r"^(the|a|an)\s+", "", v)
    v = re.sub(r"[^\w\s-]", "", v)
    return re.sub(r"\s+", " ", v)


def _same_value(a: str, b: str) -> bool:
    from canonkeeper.canon.normalize import same_country

    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    if same_country(na, nb) is True:
        return True
    if na in nb or nb in na:  # "shoulder" vs "left shoulder" is not a contradiction
        return True
    return fuzz.token_set_ratio(na, nb) >= 90


def slot_mismatch(store: CanonStore) -> list[Candidate]:
    """One candidate per disputed slot, carrying one representative claim per distinct value
    (earliest in reading order), so the adjudicator sees the whole disagreement at once."""
    out: list[Candidate] = []
    for (_subject, predicate), claims in store.slots().items():
        if predicate in MULTI_VALUED_OK or predicate not in SINGLE_VALUED:
            continue
        ordered = sorted(
            claims, key=lambda c: (c.provenance.book_index, c.provenance.chapter_index)
        )
        groups: list[list[Claim]] = []
        for c in ordered:
            for g in groups:
                if _same_value(g[0].object, c.object):
                    g.append(c)
                    break
            else:
                groups.append([c])
        if len(groups) < 2:
            continue
        reps = [g[0] for g in groups][:6]
        values = ", ".join(f"'{r.object}' ({r.provenance.locator})" for r in reps)
        times = {r.story_time for r in reps if r.story_time}
        reason = f"{reps[0].subject}.{predicate} is {values}"
        if len(times) > 1:
            reason += " (may be change over time)"
        out.append(Candidate(claims=reps, reason=reason, heuristic="slot_mismatch"))
    return out


def dead_speaks(store: CanonStore) -> list[Candidate]:
    out: list[Candidate] = []
    for entity in store.entities():
        claims = sorted(
            store.for_entity(entity),
            key=lambda c: (c.provenance.book_index, c.provenance.chapter_index),
        )
        deaths = [
            c
            for c in claims
            if (c.predicate == "dead" and _norm(c.object) in {"true", "yes", "dead"})
            or (c.kind == ClaimKind.EVENT and c.predicate == "died")
        ]
        if not deaths:
            continue
        death = deaths[0]
        pos = (death.provenance.book_index, death.provenance.chapter_index)
        for c in claims:
            cpos = (c.provenance.book_index, c.provenance.chapter_index)
            if cpos <= pos or c is death:
                continue
            if c.kind in (ClaimKind.STATE, ClaimKind.EVENT) and c.predicate not in {
                "dead",
                "died",
                "alive",
                "buried",
            }:
                out.append(
                    Candidate(
                        claims=[death, c],
                        reason=f"{entity} dies in {death.provenance.locator} but acts in {c.provenance.locator}",
                        heuristic="dead_speaks",
                    )
                )
                break  # one per entity is enough for the adjudicator
    return out


def timeline(store: CanonStore) -> list[Candidate]:
    out: list[Candidate] = []
    for (_subject, predicate), claims in store.slots().items():
        if predicate not in {"born", "birth_year", "died", "age"}:
            continue
        years: list[tuple[int, Claim]] = []
        for c in claims:
            src = c.story_time or c.object
            m = _NUM.search(src or "")
            if m:
                years.append((int(m.group(1)), c))
        vals = {y for y, _ in years}
        if len(vals) > 1 and predicate != "age":
            a, b = years[0][1], next(c for y, c in years if y != years[0][0])
            out.append(
                Candidate(
                    claims=[a, b],
                    reason=f"{a.subject}.{predicate} has conflicting years {sorted(vals)}",
                    heuristic="timeline",
                )
            )
    return out


_NAME_PREDS = {"first_name", "surname", "full_name"}


def name_drift(store: CanonStore) -> list[Candidate]:
    """One entity, two different first names / surnames (Watson: John in one story, James in
    another). Separate from slot_mismatch so the adjudicator can be told this is a *name*,
    where a nickname or a translation is the usual innocent explanation."""
    out: list[Candidate] = []
    for (_subject, predicate), claims in store.slots().items():
        if predicate not in _NAME_PREDS:
            continue
        groups: dict[str, Claim] = {}
        for c in claims:
            key = _norm(c.object)
            # Initials and abbreviations agree with the full form: "J." ~ "John", "Wm." ~ "William".
            if any(
                key.rstrip(".")[:1] == k[:1] and (key.endswith(".") or k.endswith("."))
                for k in groups
            ):
                continue
            groups.setdefault(key, c)
        if len(groups) < 2:
            continue
        for a, b in combinations(groups.values(), 2):
            out.append(
                Candidate(
                    claims=[a, b],
                    reason=f"{a.subject}.{predicate} is '{a.object}' in {a.provenance.locator} but '{b.object}' in {b.provenance.locator}",
                    heuristic="name_drift",
                )
            )
    return out


def origin_mismatch(store: CanonStore) -> list[Candidate]:
    """`nationality` says one country, `birthplace` says another. Different slots, so the
    slot screen never compares them; a demonym table does. Also catches a nationality that
    disagrees with another nationality claim by country ("Welsh" vs "German")."""
    from canonkeeper.canon.normalize import same_country

    out: list[Candidate] = []
    slots = store.slots()
    for entity in store.entities():
        key = entity.lower()
        origin = [c for p in ("nationality", "birthplace") for c in slots.get((key, p), [])]
        if len(origin) < 2:
            continue
        seen: set[frozenset[str]] = set()
        for a, b in combinations(origin, 2):
            if a.predicate == b.predicate == "birthplace":
                continue  # slot_mismatch already covers two birthplaces
            pair = frozenset((a.id, b.id))
            if pair in seen:
                continue
            seen.add(pair)
            if same_country(a.object, b.object) is False:
                out.append(
                    Candidate(
                        claims=[a, b],
                        reason=(
                            f"{a.subject}.{a.predicate} is '{a.object}' ({a.provenance.locator}) "
                            f"but {b.predicate} is '{b.object}' ({b.provenance.locator})"
                        ),
                        heuristic="origin_mismatch",
                    )
                )
    return out


UNIQUE_HOUSEHOLD_ROLES = {
    "landlady",
    "landlord",
    "housekeeper",
    "butler",
    "valet",
    "cook",
    "coachman",
    "page",
    "nurse",
    "governess",
    "secretary",
    "orderly",
}
_HOUSEHOLD_LINKS = {"employed_by", "servant_of", "landlady_of", "landlord_of", "household_of"}


def role_collision(store: CanonStore) -> list[Candidate]:
    """Two different people hold the same one-per-household role for the same household
    (Mrs. Hudson and Mrs. Turner are both 'the landlady' at Baker Street). Needs an
    `employed_by`/`servant_of`-style link to the household on both; without it, no claim."""
    holders: dict[tuple[str, str], list[tuple[str, Claim, Claim]]] = {}
    for entity in store.entities():
        claims = store.for_entity(entity)
        roles = [
            c
            for c in claims
            if c.predicate == "occupation" and _norm(c.object) in UNIQUE_HOUSEHOLD_ROLES
        ]
        links = [c for c in claims if c.predicate in _HOUSEHOLD_LINKS]
        if not roles or not links:
            continue
        for r in roles:
            for link in links:
                key = (_norm(r.object), store.canonical(link.object).lower())
                holders.setdefault(key, []).append((entity, r, link))
    out: list[Candidate] = []
    for (role, household), entries in holders.items():
        people = {e for e, _, _ in entries}
        if len(people) < 2:
            continue
        reps: dict[str, tuple[Claim, Claim]] = {}
        for e, r, link in entries:
            reps.setdefault(e, (r, link))
        claims = [r for r, _ in reps.values()]
        names = ", ".join(f"{e} ({r.provenance.locator})" for e, (r, _) in reps.items())
        out.append(
            Candidate(
                claims=claims,
                reason=f"the {role} of {household}'s household is {names}",
                heuristic="role_collision",
            )
        )
    return out


def find_candidates(store: CanonStore) -> list[Candidate]:
    cands = (
        slot_mismatch(store)
        + dead_speaks(store)
        + timeline(store)
        + name_drift(store)
        + origin_mismatch(store)
        + role_collision(store)
    )
    # De-duplicate on the claim-id pair.
    seen: set[frozenset[str]] = set()
    uniq: list[Candidate] = []
    for c in cands:
        key = frozenset(cl.id for cl in c.claims)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq
