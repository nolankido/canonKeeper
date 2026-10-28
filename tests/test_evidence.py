"""Judge review: the Watson finding quoted "I had a Jezail bullet through it some time before",
which never says "leg", and the landlady finding never named Mrs. Turner. A finding now shows
the copy of the same fact whose sentence says the thing the finding turns on."""

from __future__ import annotations

from canonkeeper.canon import CanonStore
from canonkeeper.models import AuditReport, Candidate, Claim, Finding, Provenance


def _claim(subject, predicate, obj, book, quote, start):
    return Claim(
        kind="attribute", subject=subject, predicate=predicate, object=obj,
        provenance=Provenance(book=book, book_index=int(book[:2]) - 1, chapter="I",
                              chapter_index=0, quote=quote, char_start=start, char_end=start + len(quote)),
    )  # fmt: skip


def _report(store, claims, heuristic):
    finding = Finding(
        candidate=Candidate(claims=claims, reason="r", heuristic=heuristic),
        verdict="contradiction", severity="high", explanation="e", adjudicator_model="m",
    )  # fmt: skip
    return AuditReport(series="S", books=["01", "02"], claims_total=len(store),
                       candidates_total=1, findings=[finding], models={})  # fmt: skip


def test_the_sentence_that_names_the_value_is_shown(tmp_path):
    from canonkeeper.report.evidence import telling_passages

    store = CanonStore(tmp_path / "b.jsonl")
    shoulder = _claim(
        "John Watson", "wound_location", "shoulder", "01-a", "struck on the shoulder", 0
    )
    vague = _claim("John Watson", "wound_location", "leg", "02-b", "a bullet through it", 10)
    telling = _claim("John Watson", "wound_location", "leg", "02-b", "nursing my wounded leg", 50)
    elsewhere = _claim("John Watson", "wound_location", "leg", "01-a", "a stiff leg", 90)
    for c in (shoulder, vague, telling, elsewhere):
        store.add(c)
    report = _report(store, [shoulder, vague], "slot_mismatch")
    assert telling_passages(report, store) == 1
    shown = report.findings[0].candidate.claims
    assert [c.provenance.quote for c in shown] == [
        "struck on the shoulder",
        "nursing my wounded leg",
    ]
    assert shown[1].provenance.book == "02-b"  # never a sentence from another book


def test_a_shared_role_shows_the_sentence_that_names_the_person(tmp_path):
    from canonkeeper.report.evidence import telling_passages

    store = CanonStore(tmp_path / "b.jsonl")
    a = _claim("Mrs. Turner", "occupation", "landlady", "02-b", "our landlady had provided", 0)
    b = _claim(
        "Mrs. Turner",
        "occupation",
        "landlady",
        "02-b",
        "When Mrs. Turner has brought in the tray",
        40,
    )
    hudson = _claim("Mrs. Hudson", "occupation", "landlady", "01-a", "Mrs. Hudson, our landlady", 0)
    for c in (a, b, hudson):
        store.add(c)
    report = _report(store, [hudson, a], "role_collision")
    assert telling_passages(report, store) == 1
    assert report.findings[0].candidate.claims[1].provenance.quote.startswith("When Mrs. Turner")
