"""Predicate and value normalisation.

Two books describing the same fact must land in the same slot, or the screen never sees them
together. The extractor is told to use a fixed vocabulary, but models drift ("eye_colour",
"eyes", "colour_of_eyes"), so normalisation is applied again at ingest, deterministically.
Values are normalised just enough that "Grey" / "gray" / "a grey" compare equal, without
collapsing distinctions that matter ("left shoulder" stays distinct from "shoulder" only in the
sense that one contains the other, which the screen treats as agreement).
"""

from __future__ import annotations

import re

PREDICATE_SYNONYMS: dict[str, str] = {
    # physical
    "eye_colour": "eye_color",
    "eyes": "eye_color",
    "color_of_eyes": "eye_color",
    "colour_of_eyes": "eye_color",
    "hair_colour": "hair_color",
    "hair": "hair_color",
    "color_of_hair": "hair_color",
    "colour_of_hair": "hair_color",
    "injury_location": "wound_location",
    "wound": "wound_location",
    "wounded_in": "wound_location",
    "injured_in": "wound_location",
    "scar_location": "scar",
    "stature": "height",
    # origin / identity
    "born_in": "birthplace",
    "place_of_birth": "birthplace",
    "birth_place": "birthplace",
    "hometown": "birthplace",
    "year_of_birth": "birth_year",
    "born_year": "birth_year",
    "dob": "birth_year",
    "given_name": "first_name",
    "forename": "first_name",
    "christian_name": "first_name",
    "last_name": "surname",
    "family_name": "surname",
    "name": "full_name",
    "profession": "occupation",
    "job": "occupation",
    "lives_in": "residence",
    "home": "residence",
    "address": "residence",
    # relations
    "spouse": "married_to",
    "husband": "married_to",
    "wife": "married_to",
    "married": "married_to",
    "sister_of": "sibling_of",
    "brother_of": "sibling_of",
    "sibling": "sibling_of",
    "mother_of": "parent_of",
    "father_of": "parent_of",
    "son_of": "child_of",
    "daughter_of": "child_of",
    "works_for": "employed_by",
    "employer": "employed_by",
    # state
    "deceased": "dead",
    "is_dead": "dead",
    "death": "died",
    "killed": "died",
    "is_alive": "alive",
    "whereabouts": "location",
    "current_location": "location",
}

VALUE_SYNONYMS: dict[str, str] = {
    "gray": "grey",
    "greyish": "grey",
    "grayish": "grey",
    "blond": "fair",
    "blonde": "fair",
    "golden": "fair",
    "auburn": "red",
    "ginger": "red",
    "raven": "black",
    "dead": "true",
    "deceased": "true",
    "yes": "true",
    "no": "false",
    "alive": "false",
}

_ARTICLE = re.compile(r"^(?:the|a|an|his|her|their|my)\s+", re.I)
_PUNCT = re.compile(r"[^\w\s'\-]")
_WS = re.compile(r"\s+")


def normalize_predicate(p: str) -> str:
    p = p.strip().lower().replace(" ", "_").replace("-", "_")
    p = re.sub(r"_+", "_", p).strip("_")
    return PREDICATE_SYNONYMS.get(p, p)


def normalize_claim(predicate: str, value: str) -> tuple[str, str]:
    """Predicate + value together, for the few cases where one changes the other:
    alive=false ≡ dead=true (and vice versa), so the death heuristics see one slot."""
    p = normalize_predicate(predicate)
    v = normalize_value(value, p)
    if p == "alive" and v == "false":
        return "dead", "true"
    if p == "dead" and v == "false":
        return "alive", "true"
    if p in {"dead", "alive", "died"} and v in {"", "none", "null"}:
        return p, "true"
    return p, v


def normalize_value(v: str, predicate: str | None = None) -> str:
    v = v.strip()
    if not v:
        return v
    if predicate in {"full_name", "first_name", "surname", "alias", "nickname"} or (
        predicate and predicate.endswith(("_of", "_to", "_by"))
    ):
        # Names and relation targets: keep case, just tidy whitespace/punctuation at the ends.
        return _WS.sub(" ", v.strip(" .,;:")).strip()
    low = v.lower()
    low = _ARTICLE.sub("", low)
    low = _PUNCT.sub("", low)
    low = _WS.sub(" ", low).strip()
    # Numbers: "thirty" stays as text, but "30 years" → "30".
    m = re.fullmatch(r"(\d+)(?:\s*(?:years?|yrs?|y\.?o\.?)(?:\s*old)?)?", low)
    if m:
        return m.group(1)
    return VALUE_SYNONYMS.get(low, low)
