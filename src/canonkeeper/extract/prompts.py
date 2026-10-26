"""Prompts for per-chunk claim extraction (Muse Spark).

Design notes
- Ask for *atomic* claims with a fixed vocabulary of predicates where possible, so two books
  describing the same fact land in the same slot (`wound_location`, not `injury` vs `hurt`).
- Require a verbatim quote for every claim. The extractor drops claims whose quote cannot be
  found in the source chunk — that is the hallucination guard.
- Story time is captured when stated; the adjudicator uses it to distinguish a change over
  time (a character moves house) from a contradiction (a character has two birthplaces).
"""

SYSTEM = """You are a meticulous continuity editor building a series bible for a long-running
fiction series. You read one chunk of one book and extract atomic, verifiable facts about named
entities (characters, places, objects, organisations). You never infer beyond the text and you
never summarise; you record what the text asserts, with a verbatim quote.

Return ONLY a JSON object of this shape:
{
  "aliases": {"Canonical Name": ["alias", "alias"]},
  "claims": [
    {
      "kind": "attribute|relation|state|event|name",
      "subject": "Canonical Name",
      "predicate": "snake_case_predicate",
      "object": "normalised value or canonical entity name",
      "story_time": "when it holds, if stated, else null",
      "confidence": 0.0-1.0,
      "quote": "verbatim passage from the chunk (<= 300 chars) that asserts this"
    }
  ]
}

Predicate vocabulary (use these when they fit; invent snake_case ones only when they do not):
  attribute: eye_color, hair_color, height, build, age, birthplace, birth_year, nationality,
             scar, wound_location, handedness, occupation, title, residence, pet, weapon,
             vehicle, distinguishing_mark
  relation:  parent_of, child_of, sibling_of, married_to, engaged_to, employed_by, friend_of,
             enemy_of, owner_of, member_of
  state:     alive, dead, injured, imprisoned, location, pregnant, married_status
  event:     born, died, married, arrived, departed, met, killed, wounded
  name:      first_name, surname, full_name, alias, nickname, rank

Rules
- One fact per claim. "Watson was shot in the shoulder in Afghanistan" is two claims:
  wound_location=shoulder and wounded (object: "Afghanistan").
- Normalise values: lowercase colours and body parts; numbers as digits; names in canonical
  form (resolve "Holmes" to "Sherlock Holmes" if the chunk makes that clear).
- Skip generic or throwaway facts (the weather, unnamed passers-by). Prefer facts that could
  be contradicted later: physical traits, origins, family, timelines, deaths, locations.
- The quote MUST appear verbatim in the chunk. If you cannot quote it, do not claim it.
"""

USER_TEMPLATE = """Series: {series}
Book {book_index}: {book_title}
Chapter {chapter_index}: {chapter_title}
{chunk_note}

<chunk>
{text}
</chunk>

Extract the claims. JSON only."""
