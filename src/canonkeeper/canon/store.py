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

from canonkeeper.canon.normalize import normalize_predicate, normalize_value
from canonkeeper.models import Claim, ExtractionResult


class CanonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.claims: list[Claim] = []
        self.aliases: dict[str, list[str]] = {}
        self._alias_to_canon: dict[str, str] = {}
        self._by_slot: dict[tuple[str, str], list[Claim]] = defaultdict(list)
        self._ids: set[str] = set()

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
        for canon, names in aliases.items():
            lst = self.aliases.setdefault(canon, [])
            for n in names:
                if n not in lst and n != canon:
                    lst.append(n)
                self._alias_to_canon[n.lower()] = canon
            self._alias_to_canon[canon.lower()] = canon

    def canonical(self, name: str) -> str:
        """Resolve a surface name to a canonical entity; fuzzy-match as a last resort."""
        hit = self._alias_to_canon.get(name.lower())
        if hit:
            return hit
        if self._alias_to_canon:
            best = process.extractOne(name.lower(), list(self._alias_to_canon), scorer=fuzz.WRatio)
            if best and best[1] >= 92:
                return self._alias_to_canon[best[0]]
        return name

    # --- claims ----------------------------------------------------------------------------
    def add(self, claim: Claim, *, _rebuild: bool = True) -> bool:
        if claim.id in self._ids:
            return False
        self._ids.add(claim.id)
        self.claims.append(claim)
        if _rebuild:
            self._by_slot[self._slot(claim)].append(claim)
        return True

    def ingest(self, result: ExtractionResult) -> int:
        self.register_aliases(result.aliases)
        added = 0
        for c in result.claims:
            predicate = normalize_predicate(c.predicate)
            c = c.model_copy(
                update={
                    "subject": self.canonical(c.subject),
                    "predicate": predicate,
                    "object": normalize_value(c.object, predicate),
                }
            )
            added += int(self.add(c))
        return added

    def _slot(self, claim: Claim) -> tuple[str, str]:
        return (self.canonical(claim.subject).lower(), claim.predicate.lower())

    def _reindex(self) -> None:
        self._by_slot.clear()
        for c in self.claims:
            self._by_slot[self._slot(c)].append(c)

    def slots(self) -> dict[tuple[str, str], list[Claim]]:
        self._reindex()
        return dict(self._by_slot)

    def entities(self) -> list[str]:
        return sorted({self.canonical(c.subject) for c in self.claims})

    def for_entity(self, name: str) -> list[Claim]:
        canon = self.canonical(name).lower()
        return [c for c in self.claims if self.canonical(c.subject).lower() == canon]

    def __len__(self) -> int:
        return len(self.claims)
