"""The canon store: a flat JSONL of claims plus an alias table, indexed by slot.

Flat on purpose. A series bible is a few thousand facts; a real graph database would add
operational weight without buying anything the slot index does not already give us. The file
is human-readable and diffable, which matters when an author wants to correct the bible by hand.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from rapidfuzz import fuzz, process

from canonkeeper.canon.normalize import (
    is_generic_name,
    looks_like_victim,
    names_compatible,
    normalize_claim,
    same_person_variant,
)
from canonkeeper.models import Claim, ExtractionResult


class CanonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.claims: list[Claim] = []
        self.aliases: dict[str, list[str]] = {}
        self._alias_to_canon: dict[str, str] = {}
        self._by_slot: dict[tuple[str, str], list[Claim]] = defaultdict(list)
        self._by_entity: dict[str, list[Claim]] = defaultdict(list)
        self._ids: set[str] = set()
        self._canon_cache: dict[str, str] = {}
        self._claimants: dict[str, set[str]] = {}  # alias → every entity that claimed it
        self._indexed_count = 0
        self.dropped_generic = 0
        self.last_extract_stats: object | None = None

    # --- persistence -----------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> CanonStore:
        store = cls(path)
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("_type") == "aliases":
                        store.register_aliases(rec["aliases"])
                    else:
                        store.add(Claim.model_validate(rec), _rebuild=False)
            store._reindex()
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"_type": "aliases", "aliases": self.aliases}) + "\n")
            for c in self.claims:
                f.write(c.model_dump_json(exclude={"id"}) + "\n")

    # --- aliases ---------------------------------------------------------------------------
    def register_aliases(self, aliases: Mapping[str, Iterable[str]]) -> None:
        self._canon_cache.clear()
        self._indexed_count = -1  # force a reindex: resolutions may have changed
        for canon, names in aliases.items():
            if is_generic_name(canon):
                continue
            if isinstance(names, str):
                names = [names]  # one alias given as a string, not split into its letters
            lst = self.aliases.setdefault(canon, [])
            for n in names:
                if is_generic_name(n) or len(n.strip()) < 2:
                    continue  # "he" is not a name for anyone, nor is a stray letter
                if n not in lst and n != canon:
                    lst.append(n)
                if n.lower() != canon.lower():
                    self._alias_to_canon[n.lower()] = canon
                    self._claimants.setdefault(n.lower(), set()).add(canon)
            # A name already known as someone else's alias stays that: registering "Dr.
            # Mortimer" as an entity after "James Mortimer" claimed it must not split him, and
            # the result must not depend on the order the entries arrive in (load replays them).
            self._alias_to_canon.setdefault(canon.lower(), canon)

    def _resolve(self, key: str) -> str | None:
        """Follow alias → entity links to the end ("mortimer" → "Dr. Mortimer" → "James
        Mortimer"); None when `key` is not a known name."""
        hit = self._alias_to_canon.get(key)
        chain: list[str] = []
        while hit is not None:
            if hit.lower() in (c.lower() for c in chain):
                # A cycle: two chunks each named the other as the alias ("Baskerville" ↔ "Sir
                # Henry Baskerville"). Every member must resolve to the same name whichever end
                # it is entered from, or the entity's facts are filed under a name nothing
                # looks up: the fullest name in the cycle wins.
                cycle = chain[[c.lower() for c in chain].index(hit.lower()) :]
                return max(cycle, key=lambda c: (len(c), c))
            chain.append(hit)
            nxt = self._alias_to_canon.get(hit.lower())
            if nxt is None or nxt == hit:
                break
            hit = nxt
        return hit

    def canonical(self, name: str) -> str:
        """Resolve a surface name to a canonical entity; fuzzy-match as a last resort.
        Memoised: the fuzzy path is O(aliases) and the screen asks thousands of times."""
        key = name.lower()
        cached = self._canon_cache.get(key)
        if cached is not None:
            return cached
        owners = {self._resolve(c.lower()) or c for c in self._claimants.get(key, ())}
        if len(owners) > 1:
            # "Drebber" is both Enoch J. Drebber and his son: a name two people answer to is
            # not guessed (last-registered-wins made the bible depend on arrival order).
            self._canon_cache[key] = name
            return name
        hit = self._resolve(key)
        if hit is None and self._alias_to_canon:
            # Best fuzzy match that could be the same person: "Mrs. Turner" scores 95 against
            # "Mr. Turner", and that merge once erased the canon's best-known landlady slip.
            for cand, score, _ in process.extract(
                key, list(self._alias_to_canon), scorer=fuzz.WRatio, limit=5
            ):
                if score < 92:
                    break
                target = self._resolve(cand) or cand
                if names_compatible(name, cand) and names_compatible(name, target):
                    hit = target
                    break
        result = hit or name
        self._canon_cache[key] = result
        return result

    def is_known(self, name: str) -> bool:
        """True when `name` is an alias or a canonical entity we already hold (no fuzzy)."""
        key = name.lower()
        if key in self._alias_to_canon:
            return True
        self._reindex()
        return key in self._by_entity

    # --- claims ----------------------------------------------------------------------------
    def add(self, claim: Claim, *, _rebuild: bool = True) -> bool:
        if claim.id in self._ids:
            return False
        self._ids.add(claim.id)
        self.claims.append(claim)
        if _rebuild and self._indexed_count == len(self.claims) - 1:
            # Index was current before this append: extend it. Otherwise (bulk load, or an
            # alias change that may have re-resolved earlier subjects) leave it stale and let
            # `_reindex` rebuild lazily on the next read.
            canon = self.canonical(claim.subject).lower()
            self._by_slot[(canon, claim.predicate.lower())].append(claim)
            self._by_entity[canon].append(claim)
            self._indexed_count = len(self.claims)
        return True

    def ingest(self, result: ExtractionResult) -> int:
        self.register_aliases(result.aliases)
        added = 0
        for c in result.claims:
            if is_generic_name(c.subject):
                self.dropped_generic += 1  # "he has grey eyes" with no name is not a fact
                continue
            predicate, value = normalize_claim(c.predicate, c.object)
            if predicate == "died":
                known_other = self.is_known(value) and (
                    self.canonical(value).lower() != self.canonical(c.subject).lower()
                )
                if known_other or looks_like_victim(value):
                    # "X died Y" naming a person or creature is the model saying X killed Y.
                    predicate = "killed"
                    value = self.canonical(value) if known_other else value
            c = c.model_copy(
                update={
                    "subject": self.canonical(c.subject),
                    "predicate": predicate,
                    "object": value,
                }
            )
            added += int(self.add(c))
        self.merge_variants()
        self._settle_subjects()
        return added

    def _settle_subjects(self) -> None:
        """Re-resolve every stored subject against the final alias table. Aliases learned late
        in a run (chapter 12 reveals that "the stranger" is Jefferson Hope) apply to claims made
        earlier, and the bible written to disk then reloads into exactly the same entities."""
        self._canon_cache.clear()
        changed = False
        settled: list[Claim] = []
        for c in self.claims:
            canon = self.canonical(c.subject)
            if canon != c.subject:
                c = c.model_copy(update={"subject": canon})
                changed = True
            settled.append(c)
        if changed:
            unique: dict[str, Claim] = {}
            for c in settled:
                unique.setdefault(c.id, c)
            self.claims = list(unique.values())
            self._ids = set(unique)
            self._indexed_count = -1

    def merge_variants(self) -> list[tuple[str, str]]:
        """Fold entities whose names differ only by middle names or initials into the one with
        more claims ("Enoch Drebber" → "Enoch J. Drebber"). Returns (merged, into) pairs."""
        counts: dict[str, int] = {}
        for c in self.claims:
            counts[c.subject] = counts.get(c.subject, 0) + 1
        names = sorted(counts, key=lambda n: (-counts[n], -len(n), n))
        target_of: dict[str, str] = {}
        by_surname: dict[str, list[str]] = {}
        for n in names:
            parts = n.split()
            if len(parts) < 2:
                continue
            key = parts[-1].strip(".,").lower()
            for t in by_surname.get(key, []):
                if same_person_variant(n, t):
                    target_of[n] = t
                    break
            else:
                by_surname.setdefault(key, []).append(n)
        if not target_of:
            return []
        for src, dst in target_of.items():
            moved = [src, *self.aliases.pop(src, [])]
            self.register_aliases({dst: moved})
        self.claims = [
            c.model_copy(update={"subject": target_of[c.subject]}) if c.subject in target_of else c
            for c in self.claims
        ]
        self._ids = {c.id for c in self.claims}
        self._canon_cache.clear()
        self._indexed_count = -1
        return sorted(target_of.items())

    def _slot(self, claim: Claim) -> tuple[str, str]:
        return (self.canonical(claim.subject).lower(), claim.predicate.lower())

    def _reindex(self) -> None:
        """Rebuild the slot and entity indexes; a no-op when nothing changed since last time."""
        if self._indexed_count == len(self.claims):
            return
        self._by_slot.clear()
        self._by_entity.clear()
        for c in self.claims:
            canon = self.canonical(c.subject)
            self._by_slot[(canon.lower(), c.predicate.lower())].append(c)
            self._by_entity[canon.lower()].append(c)
        self._indexed_count = len(self.claims)

    def slots(self) -> dict[tuple[str, str], list[Claim]]:
        self._reindex()
        return dict(self._by_slot)

    def entities(self) -> list[str]:
        self._reindex()
        names = {self.canonical(c.subject) for c in self.claims}
        return sorted(names)

    def for_entity(self, name: str) -> list[Claim]:
        self._reindex()
        return list(self._by_entity.get(self.canonical(name).lower(), []))

    def __len__(self) -> int:
        return len(self.claims)
