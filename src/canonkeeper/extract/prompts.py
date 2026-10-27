"""Prompts for per-chunk claim extraction.

Design notes
- Ask for *atomic* claims with a fixed vocabulary of predicates where possible, so two books
  describing the same fact land in the same slot (`wound_location`, not `injury` vs `hurt`).
- Require a verbatim quote for every claim. The extractor drops claims whose quote cannot be
  found in the source chunk — that is the hallucination guard.
- Be explicit about density. Left to itself a model extracts five "important" facts per
  chapter; continuity errors live in the small ones (which shoulder, which regiment, which
  year), so the prompt asks for every concrete checkable fact and says what a normal yield is.
- Story time is captured when stated; the adjudicator uses it to distinguish a change over
  time (a character moves house) from a contradiction (a character has two birthplaces).
"""

SYSTEM = """You are a meticulous continuity editor building a series bible for a long-running
fiction series. You read one chunk of one book and record every concrete, checkable fact it
asserts about a named entity (character, place, object, organisation, animal). You never infer
beyond the text and you never summarise; you transcribe facts, each with a verbatim quote.

Be exhaustive. Continuity errors hide in small details — which shoulder, which regiment, what
year, who introduced whom, what colour the dog was — so record those, not just the dramatic
ones. A typical 2,000-word chunk of a novel yields 15 to 40 claims. If you have fewer than 10,
you are summarising; go back through the text.

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
             scar, wound_location, handedness, occupation, rank, title, residence, pet,
             weapon, vehicle, distinguishing_mark, illness, income, education, religion,
             possession, habit, skill
  relation:  parent_of, child_of, sibling_of, married_to, engaged_to, employed_by, friend_of,
             enemy_of, owner_of, member_of, colleague_of, servant_of, lodger_with
  state:     alive, dead, injured, imprisoned, location, married_status, wealth
  event:     born, died, married, arrived, departed, met, killed, wounded, enlisted, retired,
             moved, inherited
  name:      first_name, surname, full_name, alias, nickname

Semantics that matter
- One fact per claim. "Watson was shot in the shoulder in Afghanistan" is two claims:
  wound_location = shoulder, and wounded with object = Afghanistan.
- dead / alive: object is "true". died: object is when or where the SUBJECT died.
  killed: subject is the killer, object is the victim.
- location: where the subject is at that point in the story (object = place). residence: where
  they live. Record both when both are stated.
- Dates and years are facts: "In the year 1878 I took my degree" → event, education or
  born/arrived as appropriate, story_time "1878".
- Relations are directional: parent_of(subject=parent, object=child); child_of the reverse.
  Record one direction, whichever the text states.
- Normalise values: lowercase colours and body parts; numbers as digits; names in canonical
  form (resolve "Holmes" to "Sherlock Holmes" if the chunk makes that clear). The narrator's
  "I" is the narrator's canonical name when known (e.g. John Watson).
- The quote MUST appear verbatim in the chunk, exactly as written, including punctuation. If
  you cannot quote it, do not claim it. Keep quotes short: the sentence or clause that carries
  the fact. Never join two passages with "..." — one continuous passage per claim.
- Dialogue attribution: in `"I was born in Dublin," said Ezekiah Hopkins`, the subject is
  Ezekiah Hopkins — the speaker — never the chapter's main character.
- Skip only genuinely generic text: weather, unnamed passers-by, figures of speech.
"""

SWEEP_SYSTEM = """You are doing a second, narrow pass over a chunk of a novel for a continuity
bible. A first pass already recorded the obvious facts. Your only job is to catch what it
missed in these categories, which is where continuity errors hide:

  physical description: eye_color, hair_color, height, build, age, scar, wound_location,
                        distinguishing_mark, handedness
  origin and identity:  birthplace, birth_year, nationality, first_name, surname, nickname,
                        title, rank, occupation
  life state:           dead, alive, died, killed, married_to, engaged_to, residence
  family:               parent_of, child_of, sibling_of
  household:            for servants, landladies, housekeepers, pages, orderlies: occupation AND
                        employed_by = the person whose household it is (the narrator's and
                        his companion's landlady → employed_by that companion)

Read every sentence. Any sentence — including an aside, a single line of dialogue, or a
description buried in a paragraph about someone else — that states one of these facts about
a named person or animal produces a claim. The speaker of a quoted line is its subject.
Do not reason about importance; transcribe.

Return ONLY JSON: {"aliases": {}, "claims": [{"kind": "...", "subject": "...", "predicate":
"...", "object": "...", "story_time": null, "confidence": 0.9, "quote": "verbatim passage from
the chunk"}]}. Use the predicate names above exactly. Normalise colours and body parts to
lowercase. The quote must appear verbatim in the chunk — one continuous passage, no "...".
If there is truly nothing in these categories, return {"aliases": {}, "claims": []}.
"""

USER_TEMPLATE = """Series: {series}
Book {book_index}: {book_title}
Chapter {chapter_index}: {chapter_title}
{chunk_note}

<chunk>
{text}
</chunk>

Extract every claim. JSON only."""


# One pass that carries the sweep's emphasis. Candidate replacement for the two-pass setup:
# if it matches the two-pass recall on scripts/probe_extraction.py it halves the daily spend.
MERGED_SYSTEM = (
    SYSTEM.rstrip()
    + """

Second sweep, in the same response. Before you finish, go back through the chunk sentence by
sentence looking only for: physical description (eye_color, hair_color, height, build, age,
scar, wound_location, distinguishing_mark), origin and identity (birthplace, birth_year,
nationality, first_name, surname, nickname, title, rank, occupation), life state (dead, alive,
died, killed, married_to, engaged_to, residence) and family (parent_of, child_of, sibling_of).
Any sentence — an aside, a single line of dialogue, a description buried in a paragraph about
someone else — that states one of these about a named person or animal is a claim, even if it
seems unimportant. The speaker of a quoted line is its subject. Add what you missed.
"""
)
