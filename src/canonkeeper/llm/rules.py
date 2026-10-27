"""A rule-based stand-in for the models, used by `--provider mock`.

It is not trying to be clever. It recognises a small set of sentence shapes (the same shapes
the eval harness's injection templates produce) and emits grounded claims for them, and it
rules on candidates with a fixed policy. That is enough to run the whole pipeline — build,
audit, check, eval, report — with no key, deterministically, in CI and in a demo fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

_NAME = r"([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})"

# (compiled pattern, kind, predicate, subject group, object group)
_PATTERNS: list[tuple[re.Pattern[str], str, str, int, int | str]] = [
    (re.compile(rf"{_NAME} (?:has|had) ([a-z]+) eyes\."), "attribute", "eye_color", 1, 2),
    (re.compile(rf"{_NAME} looked up, and the ([a-z]+) eyes"), "attribute", "eye_color", 1, 2),
    (re.compile(rf"{_NAME}'s eyes were ([a-z]+)\."), "attribute", "eye_color", 1, 2),
    (re.compile(rf"{_NAME} (?:has|had) ([a-z]+) hair\."), "attribute", "hair_color", 1, 2),
    (re.compile(rf"{_NAME} pushed back a lock of ([a-z]+) hair"), "attribute", "hair_color", 1, 2),
    (re.compile(rf"{_NAME} was born in ([A-Z][a-z]+)\."), "attribute", "birthplace", 1, 2),
    (re.compile(rf'"I was born in ([A-Z][a-z]+)," said {_NAME}'), "attribute", "birthplace", 2, 1),
    (re.compile(rf"The old wound in {_NAME}'s ([a-z]+),"), "attribute", "wound_location", 1, 2),
    (
        re.compile(rf"{_NAME} was (?:shot|wounded) in the ([a-z]+)\."),
        "attribute",
        "wound_location",
        1,
        2,
    ),
    (re.compile(rf"{_NAME}, being ([A-Z][a-z]+) by birth"), "attribute", "nationality", 1, 2),
    (re.compile(rf"{_NAME} was ([A-Z][a-z]+) by birth\."), "attribute", "nationality", 1, 2),
    (
        re.compile(
            rf'"([A-Z][a-z]+)," she said softly, using the name only his family used for {_NAME}'
        ),
        "name",
        "first_name",
        2,
        1,
    ),
    (re.compile(rf"{_NAME} died\."), "event", "died", 1, "true"),
    (re.compile(rf"since {_NAME} had been laid in the ground"), "state", "dead", 1, "true"),
    (re.compile(rf"{_NAME} walked to ([A-Z][a-z]+)\."), "state", "location", 1, 2),
    (re.compile(rf"{_NAME} married {_NAME}\."), "relation", "married_to", 1, 2),
    (re.compile(rf"{_NAME} was the sister of {_NAME}\."), "relation", "sibling_of", 1, 2),
    (re.compile(rf"{_NAME} was born in (1[5-9]\d\d)\."), "event", "born", 1, 2),
    (re.compile(rf"{_NAME} was known to everyone as ([A-Z][a-z]+)\."), "name", "first_name", 1, 2),
]


def extract(text: str) -> dict:
    claims = []
    for pat, kind, predicate, sg, og in _PATTERNS:
        for m in pat.finditer(text):
            obj = m.group(og) if isinstance(og, int) else og
            claims.append(
                {
                    "kind": kind,
                    "subject": m.group(sg),
                    "predicate": predicate,
                    "object": obj,
                    "quote": m.group(0),
                }
            )
    return {"claims": claims, "aliases": {}}


_CAND = re.compile(r"^## Candidate (\d+) \((\w+)\)\n(.*?)$", re.M)
_BLOCKS = re.compile(r"(?=^## Candidate \d+ )", re.M)

# Phrases that, when they appear in the *later* passage, mark an in-text explanation. A real
# adjudicator reads the whole chapter; the stand-in just looks for the author saying so.
_REVEAL_MARKERS = (
    "had been a trick",
    "had always been",
    "had lied",
    "the truth was",
    "had never really been",
    "all along",
)


_QUOTE = re.compile(r"quote: “(.*?)”")


def _explained_nearby(block: str, context: str, window: int = 400) -> str | None:
    """The explaining text if any quoted passage is followed, in the same paragraph of the
    series text and within `window` chars, by an explicit explanation marker."""
    for q in _QUOTE.findall(block):
        i = context.find(q)
        if i == -1:
            continue
        start = i + len(q)
        end = context.find("\n\n", start)
        end = start + window if end == -1 else min(end, start + window)
        tail = context[start:end]
        if any(marker in tail.lower() for marker in _REVEAL_MARKERS):
            return tail.strip()
    return None


def _paragraph_around(block: str, context: str) -> str | None:
    """The paragraph of the series text containing the first quoted passage."""
    for q in _QUOTE.findall(block):
        i = context.find(q)
        if i == -1:
            continue
        start = context.rfind("\n\n", 0, i) + 2 if context.rfind("\n\n", 0, i) != -1 else 0
        end = context.find("\n\n", i)
        para = context[start : end if end != -1 else len(context)].strip()
        if len(para) > len(q) + 10:
            return para
    return None


def adjudicate(user: str, context: str = "") -> dict:
    """Fixed policy: an explanation marker right after a quoted passage (in the series text) or
    'may be change over time' in the reason → deliberate; everything else → contradiction."""
    verdicts = []
    blocks = {int(m.group(1)): b for b in _BLOCKS.split(user) if (m := _CAND.match(b))}
    for m in _CAND.finditer(user):
        cid, heuristic, reason = int(m.group(1)), m.group(2), m.group(3)
        block = blocks.get(cid, "")
        explained = _explained_nearby(block, context)
        if explained:
            verdicts.append(
                {
                    "id": cid,
                    "verdict": "deliberate",
                    "severity": "low",
                    "explanation": "The later passage explains the change explicitly.",
                    "evidence": explained,
                }
            )
            continue
        if "may be change over time" in reason:
            verdicts.append(
                {
                    "id": cid,
                    "verdict": "deliberate",
                    "severity": "low",
                    "explanation": "Change over time supported by the text.",
                    "evidence": _paragraph_around(block, context),
                }
            )
        else:
            sev = "high" if heuristic in {"dead_speaks", "name_drift"} else "medium"
            verdicts.append(
                {
                    "id": cid,
                    "verdict": "contradiction",
                    "severity": sev,
                    "explanation": "The two passages assert different values for the same fact.",
                    "suggested_fix": "Change the later passage to match the earlier one.",
                }
            )
    return {"verdicts": verdicts}


def handler(model: str, messages: list[dict[str, str]]) -> str:
    user = messages[-1]["content"]
    if "<chunk>" in user:
        chunk = user.split("<chunk>", 1)[1].split("</chunk>", 1)[0]
        return json.dumps(extract(chunk))
    if "## Candidate" in user:
        context = messages[1]["content"] if len(messages) > 2 else ""
        return json.dumps(adjudicate(user, context))
    return "{}"


Handler = Callable[[str, list[dict[str, str]]], str]
