"""Local, zero-cost candidate detection.

The adjudicator is the expensive model and we want it to spend its attention on things
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
# Single-valued slots that legitimately hold more than one value (two parents).
MAX_DISTINCT = {"child_of": 2}
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


_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
}
# A hair or eye "colour" with none of these is a texture or a vague remark ("curly",
# "fluffy", "peculiar tint") and cannot contradict a colour.
_COLOUR_WORDS = {
    "black",
    "brown",
    "dark",
    "fair",
    "blond",
    "blonde",
    "golden",
    "red",
    "auburn",
    "ginger",
    "chestnut",
    "grey",
    "gray",
    "grizzled",
    "white",
    "silver",
    "sandy",
    "light",
    "hazel",
    "blue",
    "green",
    "amber",
    "violet",
    "yellow",
    "flaxen",
    "raven",
    "tawny",
    "pale",
}


def _norm(v: str) -> str:
    v = v.strip().lower()
    v = re.sub(r"^(the|a|an)\s+", "", v)
    v = re.sub(r"[^\w\s-]", "", v)
    v = " ".join(_NUMBER_WORDS.get(t, t) for t in v.split())
    return re.sub(r"\s+", " ", v)


def _has_colour(v: str) -> bool:
    return any(t in _COLOUR_WORDS for t in re.split(r"[\s-]+", v.lower()))


def _same_value(a: str, b: str, predicate: str | None = None) -> bool:
    from canonkeeper.canon.normalize import same_place

    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    if predicate in ("hair_color", "eye_color") and not (_has_colour(na) and _has_colour(nb)):
        return True  # nothing to compare
    if predicate in ("birthplace", "nationality"):
        place = same_place(na, nb)
        if place is not None:
            return place
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
                if _same_value(g[0].object, c.object, predicate):
                    g.append(c)
                    break
            else:
                groups.append([c])
        if len(groups) <= MAX_DISTINCT.get(predicate, 1):
            continue
        reps = [g[0] for g in groups][:6]
        values = ", ".join(f"'{r.object}' ({r.provenance.locator})" for r in reps)
        times = {r.story_time for r in reps if r.story_time}
        reason = f"{reps[0].subject}.{predicate} is {values}"
        if len(times) > 1:
            reason += " (may be change over time)"
        out.append(Candidate(claims=reps, reason=reason, heuristic="slot_mismatch"))
    return out


def _is_death(c: Claim, store: CanonStore, entities: set[str]) -> bool:
    """`dead=true`, or a `died` event whose object is not another character. Models sometimes
    emit `X died Y` for "X shot Y"; the ingest normaliser rewrites those to `killed` when Y
    is already known, and this guard catches the ones learned later."""
    if c.predicate == "dead":
        return _norm(c.object) in {"true", "yes", "dead"}
    if c.kind == ClaimKind.EVENT and c.predicate == "died":
        from canonkeeper.canon.normalize import looks_like_victim

        other = store.canonical(c.object).lower()
        if other in entities and other != store.canonical(c.subject).lower():
            return False
        return not looks_like_victim(c.object)
    return False


def dead_speaks(store: CanonStore) -> list[Candidate]:
    out: list[Candidate] = []
    entities = {e.lower() for e in store.entities()}
    for entity in store.entities():
        claims = sorted(
            store.for_entity(entity),
            key=lambda c: (c.provenance.book_index, c.provenance.chapter_index),
        )
        deaths = [c for c in claims if _is_death(c, store, entities)]
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
    slot screen never compares them; a demonym table does. Two nationalities (or two
    birthplaces) are slot_mismatch's job; raising them here too sent Dr. Becher's 'German?' /
    'an Englishman' to the adjudicator twice on the Holmes run."""
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
            if a.predicate == b.predicate:
                continue  # two birthplaces or two nationalities: slot_mismatch covers those
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
_LODGING_ROLES = {"landlady", "landlord"}
_HOUSEHOLD_LINKS = {"employed_by", "servant_of", "landlady_of", "landlord_of", "household_of"}


def role_collision(store: CanonStore) -> list[Candidate]:
    """Two different people hold the same one-per-household role for the same household
    (Mrs. Hudson and Mrs. Turner are both 'the landlady' at Baker Street).

    A holder is tied to a household by an `employed_by`/`servant_of`-style link. A holder
    with *no* link gets a weak tie instead, to a household head already known for that role
    when the head has facts in the same chapter as the role claim.

    Lodging roles (landlady, landlord) can also attach to a protagonist (the two entities with
    the most facts): series heroes lodge, and nobody writes "Mrs. Hudson, employed by Holmes"
    — she is just "our landlady". That tie needs an anchor, someone holding the role in at
    least two chapters, so a one-scene innkeeper the hero passes through does not count. On
    the Holmes bible this raises Hudson/Turner and nothing else; extending protagonist ties to
    butlers and housekeepers raised a dozen households the detective merely visits."""
    role_claims: dict[str, list[tuple[str, Claim]]] = {}
    links_of: dict[str, list[Claim]] = {}
    for entity in store.entities():
        claims = store.for_entity(entity)
        roles = [
            c
            for c in claims
            if c.predicate == "occupation" and _norm(c.object) in UNIQUE_HOUSEHOLD_ROLES
        ]
        if not roles:
            continue
        links_of[entity] = [c for c in claims if c.predicate in _HOUSEHOLD_LINKS]
        for r in roles:
            role_claims.setdefault(_norm(r.object), []).append((entity, r))

    # Who is a subject in which chapter — the evidence for a weak tie.
    present: dict[tuple[int, int], set[str]] = {}
    for c in store.claims:
        present.setdefault((c.provenance.book_index, c.provenance.chapter_index), set()).add(
            store.canonical(c.subject).lower()
        )

    counts: dict[str, int] = {}
    for c in store.claims:
        k = store.canonical(c.subject)
        counts[k] = counts.get(k, 0) + 1
    protagonists = sorted(counts, key=lambda n: -counts[n])[:2]

    out: list[Candidate] = []
    emitted: set[frozenset[str]] = set()
    for role, entries in role_claims.items():
        # Strong ties first: (household head) -> {entity: (role claim, link, strong?)}
        heads: dict[str, dict[str, tuple[Claim, bool]]] = {}
        display: dict[str, str] = {}
        for entity, r in entries:
            for link in links_of[entity]:
                head = store.canonical(link.object).lower()
                display.setdefault(head, store.canonical(link.object))
                heads.setdefault(head, {}).setdefault(entity, (r, True))
        if role in _LODGING_ROLES:
            chapters: dict[str, set[tuple[int, int]]] = {}
            for entity, r in entries:
                chapters.setdefault(entity, set()).add(
                    (r.provenance.book_index, r.provenance.chapter_index)
                )
            anchors = {e for e, ch in chapters.items() if len(ch) >= 2}
            for p in protagonists:
                for entity, r in entries:
                    scene = (r.provenance.book_index, r.provenance.chapter_index)
                    if entity in anchors and p.lower() in present.get(scene, set()):
                        heads.setdefault(p.lower(), {}).setdefault(entity, (r, False))
                        display.setdefault(p.lower(), p)
        if not heads:
            continue
        for entity, r in entries:
            if links_of[entity]:
                continue
            scene = (r.provenance.book_index, r.provenance.chapter_index)
            for head in heads:
                if head in present.get(scene, set()):
                    heads[head].setdefault(entity, (r, False))
        for head, people in heads.items():
            if len(people) < 2:
                continue
            ids = frozenset(r.id for r, _ in people.values())
            if ids in emitted:
                continue  # Holmes's and Watson's households are the same rooms
            emitted.add(ids)
            weak = [e for e, (_, strong) in people.items() if not strong]
            names = ", ".join(f"{e} ({r.provenance.locator})" for e, (r, _) in people.items())
            reason = f"the {role} of {display[head]}'s household is {names}"
            if weak:
                reason += (
                    f" (tie for {', '.join(weak)} is only that {display[head]} is in the scene)"
                )
            out.append(
                Candidate(
                    claims=[r for r, _ in people.values()],
                    reason=reason,
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
