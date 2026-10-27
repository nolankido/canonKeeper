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
    "flaxen": "fair",
    "tow-coloured": "fair",
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


# Surface forms that name nobody in particular. Models offer them as aliases ("he", "my
# friend", "the stranger"); accepting them would route every later "he" to one character.
_PRONOUNS = {
    "i",
    "me",
    "my",
    "mine",
    "myself",
    "you",
    "your",
    "yours",
    "yourself",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "we",
    "us",
    "our",
    "ours",
    "they",
    "them",
    "their",
    "theirs",
    "who",
    "whom",
    "one",
    "someone",
    "somebody",
    "nobody",
}
_GENERIC_NOUNS = (
    "friend|companion|comrade|master|servant|man|woman|lady|gentleman|boy|girl|fellow|lodger|"
    "stranger|visitor|client|guest|doctor|detective|inspector|narrator|speaker|reader|person|"
    "unknown|other|same|latter|former|deceased|prisoner|victim|witness|lad|chap|sir|madam|"
    "guv'nor|guvnor|"
    # household and street roles: "the landlady" is a different person in every house
    "landlady|landlord|housekeeper|butler|maid|cook|coachman|groom|page|clerk|constable|"
    "sergeant|officer|policeman|cabman|driver|porter|waiter|messenger|commissionaire"
)
_GENERIC = re.compile(
    rf"^(?:(?:my|our|his|her|your|their|the|a|an|this|that|these|those|some|old|young|poor|dear|"
    rf"fellow|good)[\s-]+)*(?:{_GENERIC_NOUNS})s?$",
    re.I,
)


def is_generic_name(name: str) -> bool:
    """True for pronouns and descriptions that could be anyone ("he", "the man", "my friend")."""
    n = _WS.sub(" ", name.strip().strip(".,;:'\"")).lower()
    return not n or n in _PRONOUNS or bool(_GENERIC.match(n))


# --- personal names ------------------------------------------------------------------------
_MALE = {
    "mr",
    "mister",
    "sir",
    "lord",
    "master",
    "monsieur",
    "m",
    "herr",
    "signor",
    "senor",
    "señor",
    "father",
    "brother",
    "uncle",
    "king",
    "prince",
    "duke",
    "count",
    "baron",
    "earl",
}
_FEMALE = {
    "mrs",
    "miss",
    "ms",
    "madam",
    "madame",
    "mme",
    "mademoiselle",
    "mlle",
    "lady",
    "dame",
    "frau",
    "fraulein",
    "fräulein",
    "signora",
    "senora",
    "señora",
    "sister",
    "aunt",
    "mother",
    "queen",
    "princess",
    "duchess",
    "countess",
    "baroness",
}
_NEUTRAL = {
    "dr",
    "doctor",
    "professor",
    "prof",
    "rev",
    "reverend",
    "captain",
    "capt",
    "colonel",
    "col",
    "major",
    "inspector",
    "sergeant",
    "sgt",
    "lieutenant",
    "lt",
    "general",
    "gen",
    "admiral",
    "constable",
    "judge",
    "elder",
    "young",
    "old",
    "the",
    "st",
    "saint",
}
_SUFFIX = {"jr", "sr", "junior", "senior", "esq", "ii", "iii"}


def name_parts(name: str) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    """(gender from honorific: "m"/"f"/None, given name, surname, middle tokens).
    "Mrs. Hudson" → ("f", None, "hudson", ()); "Dr. John H. Watson" → (None, "john",
    "watson", ("h",)). Leading honorifics and trailing suffixes are not part of the name."""
    toks = [t.strip(".,'\"()").lower() for t in name.replace("-", " ").split()]
    toks = [t for t in toks if t]
    gender: str | None = None
    while toks and (toks[0] in _MALE or toks[0] in _FEMALE or toks[0] in _NEUTRAL):
        t = toks.pop(0)
        if t in _MALE:
            gender = gender or "m"
        elif t in _FEMALE:
            gender = gender or "f"
    while toks and toks[-1] in _SUFFIX:
        toks.pop()
    if not toks:
        return gender, None, None, ()
    if len(toks) == 1:
        return gender, None, toks[0], ()
    if gender is None:  # "Susan Cushing" is not "Mr. Cushing"
        gender = "f" if toks[0] in _FEMALE_GIVEN else "m" if toks[0] in _MALE_GIVEN else None
    return gender, toks[0], toks[-1], tuple(toks[1:-1])


def names_compatible(a: str, b: str) -> bool:
    """False when two names cannot be one person: honorifics of different gender
    (Mr./Mrs. Turner), or two different spelled-out given names (John/Joan Turner)."""
    ga, fa, sa, _ = name_parts(a)
    gb, fb, sb, _ = name_parts(b)
    if ga and gb and ga != gb:
        return False
    if fa and fb and fa != fb and len(fa) > 1 and len(fb) > 1:
        return False
    return not (sa and sb and sa != sb and fa is None and fb is None)


def same_person_variant(a: str, b: str) -> bool:
    """Two full names that differ only by middle names/initials or honorifics:
    "Enoch J. Drebber" ~ "Enoch Drebber", "Dr. John H. Watson" ~ "John Watson". Both need a
    spelled-out given name and the same surname; a bare surname never merges ("Mr. Turner"
    is not "John Turner" — there are two Turners in the canon)."""
    ga, fa, sa, ma = name_parts(a)
    gb, fb, sb, mb = name_parts(b)
    if not (fa and fb and sa and sb) or fa != fb or sa != sb or len(fa) < 2:
        return False
    if ga and gb and ga != gb:
        return False
    # middles must agree where both are present: "Enoch J." vs "Enoch K." stays apart
    return all(x[0] == y[0] for x, y in zip(ma, mb, strict=False))


# --- "died: <victim>" -----------------------------------------------------------------------
# Models write `X died Y` for "X shot Y" despite the prompt. A legitimate `died` object is a
# place, a time or a cause ("utrecht", "1876", "fright"); a misuse names a person or creature
# ("islander", "two warders", "henry staunton"). Values are lowercased by then, so a small
# vocabulary decides.
_AGENT_NOUNS = {
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "girl",
    "child",
    "baby",
    "father",
    "mother",
    "husband",
    "wife",
    "son",
    "daughter",
    "brother",
    "sister",
    "uncle",
    "aunt",
    "cousin",
    "guard",
    "guards",
    "warder",
    "warders",
    "watchman",
    "surgeon",
    "mate",
    "sailor",
    "seaman",
    "servant",
    "coachman",
    "groom",
    "keeper",
    "constable",
    "officer",
    "policeman",
    "soldier",
    "sepoy",
    "islander",
    "savage",
    "native",
    "hound",
    "dog",
    "beast",
    "creature",
    "horse",
    "prisoner",
    "convict",
    "stranger",
    "victim",
    "gentleman",
    "clerk",
    "partner",
    "rival",
    "enemy",
    "companion",
    "assailant",
    "attacker",
    "him",
    "her",
    "them",
    "lover",
    "butler",
}
_GIVEN_NAMES = {
    "john",
    "james",
    "william",
    "henry",
    "george",
    "charles",
    "thomas",
    "edward",
    "arthur",
    "robert",
    "richard",
    "joseph",
    "francis",
    "frederick",
    "albert",
    "alfred",
    "walter",
    "harry",
    "samuel",
    "peter",
    "michael",
    "patrick",
    "hugh",
    "ralph",
    "victor",
    "jack",
    "jim",
    "joe",
    "tom",
    "bill",
    "dick",
    "ned",
    "jonathan",
    "jacob",
    "isaac",
    "abe",
    "abraham",
    "enoch",
    "elias",
    "hilton",
    "ronald",
    "reginald",
    "percy",
    "neville",
    "godfrey",
    "lucas",
    "eduardo",
    "mary",
    "elizabeth",
    "sarah",
    "anne",
    "ann",
    "jane",
    "margaret",
    "emily",
    "alice",
    "helen",
    "julia",
    "lucy",
    "violet",
    "agnes",
    "edith",
    "ellen",
    "emma",
    "kate",
    "irene",
    "hattie",
    "hatty",
    "effie",
    "maud",
    "laura",
    "rachel",
    "susan",
    "martha",
    "catherine",
    "isabel",
    "beatrice",
    "eva",
    "flora",
    "grace",
    "louisa",
    "nancy",
    "rose",
}


_FEMALE_GIVEN = {
    "mary",
    "elizabeth",
    "sarah",
    "anne",
    "ann",
    "jane",
    "margaret",
    "emily",
    "alice",
    "helen",
    "julia",
    "lucy",
    "violet",
    "agnes",
    "edith",
    "ellen",
    "emma",
    "kate",
    "irene",
    "hattie",
    "hatty",
    "effie",
    "maud",
    "laura",
    "rachel",
    "susan",
    "martha",
    "catherine",
    "isabel",
    "beatrice",
    "eva",
    "flora",
    "grace",
    "louisa",
    "nancy",
    "rose",
    "annie",
    "theresa",
    "ivy",
    "hester",
    "elsie",
    "mabel",
    "sophy",
    "amelia",
    "charlotte",
}
_MALE_GIVEN = _GIVEN_NAMES - _FEMALE_GIVEN


def looks_like_victim(value: str) -> bool:
    """True when a `died` object names a person or creature rather than a place/time/cause."""
    toks = [t.strip(".,'\"()") for t in value.lower().replace("'s", " ").split()]
    toks = [t for t in toks if t and t not in {"the", "a", "an", "two", "three", "his", "her"}]
    if not toks:
        return False
    if any(t in _AGENT_NOUNS for t in toks):
        return True
    return toks[0] in _GIVEN_NAMES and len(toks) <= 3 and not any(ch.isdigit() for ch in value)


# Demonym → country/region, for comparing `nationality` against `birthplace`. Small on purpose:
# a conflict is only raised when both sides map to a known place and the places differ.
DEMONYMS: dict[str, str] = {
    "english": "england",
    "englishman": "england",
    "englishwoman": "england",
    "scottish": "scotland",
    "scot": "scotland",
    "scotsman": "scotland",
    "welsh": "wales",
    "welshman": "wales",
    "irish": "ireland",
    "irishman": "ireland",
    "british": "britain",
    "briton": "britain",
    "american": "united states",
    "yankee": "united states",
    "french": "france",
    "frenchman": "france",
    "german": "germany",
    "italian": "italy",
    "spanish": "spain",
    "spaniard": "spain",
    "russian": "russia",
    "greek": "greece",
    "dutch": "netherlands",
    "dutchman": "netherlands",
    "swiss": "switzerland",
    "norwegian": "norway",
    "swedish": "sweden",
    "danish": "denmark",
    "australian": "australia",
    "canadian": "canada",
    "indian": "india",
    "chinese": "china",
    "japanese": "japan",
    "bohemian": "bohemia",
}

# Places inside a country, so "born in Pershore" does not contradict "an Englishman".
REGIONS: dict[str, str] = {
    "england": "england",
    "london": "england",
    "worcestershire": "england",
    "pershore": "england",
    "devonshire": "england",
    "devon": "england",
    "norfolk": "england",
    "sussex": "england",
    "yorkshire": "england",
    "kent": "england",
    "surrey": "england",
    "hampshire": "england",
    "portsmouth": "england",
    "birmingham": "england",
    "chesterfield": "england",
    "wales": "wales",
    "cardiff": "wales",
    "swansea": "wales",
    "scotland": "scotland",
    "edinburgh": "scotland",
    "glasgow": "scotland",
    "aberdeen": "scotland",
    "ireland": "ireland",
    "dublin": "ireland",
    "belfast": "ireland",
    "britain": "britain",
    "great britain": "britain",
    "united states": "united states",
    "america": "united states",
    "usa": "united states",
    "cleveland": "united states",
    "ohio": "united states",
    "utah": "united states",
    "california": "united states",
    "pennsylvania": "united states",
    "new york": "united states",
    "france": "france",
    "paris": "france",
    "montpellier": "france",
    "germany": "germany",
    "berlin": "germany",
    "frankfort": "germany",
    "australia": "australia",
    "ballarat": "australia",
    "victoria": "australia",
    "greece": "greece",
    "athens": "greece",
    "india": "india",
    "bombay": "india",
    "agra": "india",
    "salt lake city": "united states",
    "chicago": "united states",
    "san francisco": "united states",
    "philadelphia": "united states",
    "boston": "united states",
    "atlanta": "united states",
    "georgia": "united states",
    "st louis": "united states",
    "missouri": "united states",
    "nevada": "united states",
    "south australia": "australia",
    "new south wales": "australia",
    "adelaide": "australia",
    "sydney": "australia",
    "melbourne": "australia",
}

# Places that are a single town or city. Two *different* cities in one country disagree
# ("born in Edinburgh" / "born in Glasgow"); a city and its own region or country do not
# ("Adelaide" / "South Australia", "Salt Lake City" / "America").
CITIES = {
    "london",
    "pershore",
    "portsmouth",
    "birmingham",
    "chesterfield",
    "cardiff",
    "swansea",
    "edinburgh",
    "glasgow",
    "aberdeen",
    "dublin",
    "belfast",
    "cleveland",
    "paris",
    "montpellier",
    "berlin",
    "frankfort",
    "ballarat",
    "athens",
    "bombay",
    "agra",
    "salt lake city",
    "chicago",
    "san francisco",
    "philadelphia",
    "boston",
    "atlanta",
    "st louis",
    "adelaide",
    "sydney",
    "melbourne",
}

_COUNTRY_OF = {"england": "britain", "wales": "britain", "scotland": "britain"}


def _place_key(value: str) -> str | None:
    """The most specific known place or demonym named in `value` (multi-word names first)."""
    v = " " + _WS.sub(" ", value.strip().lower().replace(",", " ").replace(".", "")) + " "
    for key in sorted([*REGIONS, *DEMONYMS], key=len, reverse=True):
        if f" {key} " in v:
            return key
    return None


def place_country(value: str) -> str | None:
    """Country for a birthplace or nationality value, or None if unknown."""
    key = _place_key(value)
    if key is None:
        return None
    return DEMONYMS.get(key) or REGIONS.get(key)


def same_place(a: str, b: str) -> bool | None:
    """True when two birthplaces/nationalities can both be right, False when they cannot,
    None when either is unknown. A city agrees with its own country or region; two different
    cities never agree, even in one country."""
    ka, kb = _place_key(a), _place_key(b)
    if ka is None or kb is None:
        return None
    if ka == kb:
        return True
    if ka in CITIES and kb in CITIES:
        return False
    return same_country(a, b)


def same_country(a: str, b: str) -> bool | None:
    """True/False when both map to a known country; None when either is unknown.
    England/Wales/Scotland each count as agreeing with Britain."""
    ca, cb = place_country(a), place_country(b)
    if ca is None or cb is None:
        return None
    if ca == cb:
        return True
    return _COUNTRY_OF.get(ca, ca) == _COUNTRY_OF.get(cb, cb) and "britain" in (ca, cb)
