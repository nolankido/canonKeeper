"""Adjudication prompts.

Message layout is chosen for prompt caching and batching:

    system   — static instructions
    user #1  — the series text (large, identical across calls when the series fits the budget)
    user #2  — a batch of candidate conflicts, each with quotes and the bible's other facts

The large, unchanging prefix comes first so an OpenAI-compatible host can serve it from cache
(providers bill cached input at a fraction of the full price); the part that varies comes last.
One call rules on many candidates, so the cost of reading the series is paid once per batch,
not once per candidate.
"""

from canonkeeper.models import Claim

SYSTEM = """You are the senior continuity editor for a multi-book fiction series. You will be
given the text of the series (or as much as fits), then a numbered list of candidate conflicts:
sets of facts the series bible recorded about one entity that appear to disagree, with their
verbatim source passages. For EACH candidate, decide what is going on.

Verdicts
- "contradiction": the passages cannot both be true as written; a careful reader would call it
                   an error.
- "deliberate":    the difference is explained by the story — a reveal, a lie by a character, a
                   disguise, an unreliable narrator, or a change over time the text supports.
- "ambiguous":     the passages differ but the text is loose enough that either could be read
                   as consistent.
- "not_conflict":  the bible extraction was wrong — different entities, a mislabelled predicate,
                   or two spellings of the same value.

Severity (meaningful for "contradiction")
- high:   facts readers track — deaths, family, names, origins, major injuries.
- medium: physical description, dates off by a little, geography.
- low:    minor props, phrasing-level slips.

Each candidate names the screen that raised it. What they mean:
- slot_mismatch:   one entity, one single-valued attribute, two values. Before calling a
                   physical description a contradiction, read the scene: a character in
                   disguise or costume, seen by someone who is deceived, or described as they
                   were years earlier is "deliberate" when the text says so.
- dead_speaks:     an entity is dead at one point and acts later. A flashback, a narrated
                   backstory, or a report of past deeds is not a conflict; a scene set after
                   the death in which the person acts is.
- name_drift:      two different given names or surnames for one person. A woman's maiden
                   and married surnames, a nickname or pet name, a title, or an alias the
                   character is known to use are not contradictions when the text supports
                   them (a marriage, "she was born a ...", "known as ...").
- origin_mismatch: nationality and birthplace point to different countries.
- timeline:        years that go backwards for a birth or death.
- role_collision:  two different people hold a one-per-household role (the landlady, the
                   butler) in the same household. If the text explains a change of staff it is
                   "deliberate"; if the second person simply appears in the role with no
                   explanation and the first is still in it elsewhere, it is a "contradiction",
                   severity medium, and the fix renames the person in the later passage. The
                   screen may have tied the second person to the household only because the
                   household's head is in the scene; check the scene, and if the person
                   plainly belongs to another house it is "not_conflict".

Be concrete: quote the exact words that create the conflict. For a "contradiction", propose the
smallest edit to the LATER passage that fixes it. Earlier books are already published; never
propose changing them.

Clearing a conflict needs proof. For "deliberate" and "not_conflict", put in `evidence` a
passage copied exactly from the series text above (up to ~300 characters) that shows why:
- deliberate: the passage where the story explains the difference (the disguise, the lie, the
  passage of years). Not one of the conflicting quotes on its own.
- not_conflict: the passage that shows the bible misread it (e.g. the sentence where "died"
  really means the subject killed someone).
If you cannot point to such a passage, the verdict is "contradiction" or "ambiguous" — never
infer a reveal, a character development or a mislabel that the text does not state.

Return ONLY JSON of this shape, one entry per candidate, using the candidate's id:
{"verdicts": [
  {"id": 1, "verdict": "contradiction|deliberate|ambiguous|not_conflict",
   "severity": "high|medium|low", "explanation": "...", "suggested_fix": "... or null",
   "evidence": "exact passage from the text, or null"}
]}
"""

CONTEXT_TEMPLATE = """Series: {series}

The series text follows, in reading order. Chapter headings are marked with `###`.

{context_block}"""

NO_CONTEXT = "(no source text available; judge from the quotes and the bible alone)"

BATCH_TEMPLATE = """Now the candidates. Judge each one. Return JSON only.

{candidates_block}"""

CANDIDATE_TEMPLATE = """## Candidate {id} ({heuristic})
{reason}

{claims_block}

Other facts the bible holds about {entity}:
{entity_block}
"""


def claim_line(i: int, c: Claim) -> str:
    when = f" (story time: {c.story_time})" if c.story_time else ""
    return (
        f"[{i}] {c.subject} · {c.predicate} = {c.object}{when}\n"
        f"    source: Book {c.provenance.book_index + 1} “{c.provenance.book}”, {c.provenance.chapter}\n"
        f"    quote: “{c.provenance.quote}”"
    )
