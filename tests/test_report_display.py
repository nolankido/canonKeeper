from canonkeeper.models import Candidate, Claim, ClaimKind, Provenance
from canonkeeper.report.display import chapter, check, fact, headline, where


def _c(subject, predicate, obj, book_index, title, ch="I", ch_index=0):
    return Claim(
        kind=ClaimKind.ATTRIBUTE,
        subject=subject,
        predicate=predicate,
        object=obj,
        provenance=Provenance(
            book=f"0{book_index + 1}",
            book_index=book_index,
            book_title=title,
            chapter=ch,
            chapter_index=ch_index,
            quote="q",
        ),
    )


def test_chapter_titles_are_unshouted_but_mixed_case_is_kept():
    assert chapter("X. THE ADVENTURE OF THE NOBLE BACHELOR") == (
        "X. The Adventure of the Noble Bachelor"
    )
    assert chapter("MR. SHERLOCK HOLMES.") == "Mr. Sherlock Holmes"
    assert chapter("CHAPTER IV.") == "Chapter IV"
    assert chapter("The Science of Deduction") == "The Science of Deduction"


def test_headlines_read_as_sentences_grouped_by_book():
    a = _c("John Watson", "wound_location", "shoulder", 0, "A Study in Scarlet")
    b = _c("John Watson", "wound_location", "left arm", 0, "A Study in Scarlet", ch_index=2)
    c = _c("John Watson", "wound_location", "leg", 1, "The Sign of the Four")
    h = headline(Candidate(claims=[c, a, b], reason="r", heuristic="slot_mismatch"))
    assert h == (
        "John Watson’s wound: shoulder / left arm in A Study in Scarlet vs leg in The Sign of the Four"
    )
    same = headline(Candidate(claims=[a, b], reason="r", heuristic="slot_mismatch"))
    assert same == "John Watson’s wound: shoulder vs left arm (both in A Study in Scarlet)"

    n = _c("Dr. Becher", "nationality", "german", 2, "Adventures")
    o = _c("Dr. Becher", "birthplace", "london", 2, "Adventures")
    assert headline(Candidate(claims=[n, o], reason="r", heuristic="origin_mismatch")) == (
        "Dr. Becher: nationality German in Adventures, but birthplace London in the same book"
    )

    d = _c("Holmes", "died", "true", 3, "Memoirs", ch="XII. THE FINAL PROBLEM")
    e = _c("Holmes", "spoke", "x", 4, "Hound", ch="SIR HENRY BASKERVILLE")
    r = f"Holmes dies in {d.provenance.locator} but acts in {e.provenance.locator}"
    assert headline(Candidate(claims=[d, e], reason=r, heuristic="dead_speaks")) == (
        "Holmes dies in Memoirs · XII. The Final Problem but appears again in "
        "Hound · Sir Henry Baskerville"
    )
    t = headline(
        Candidate(
            claims=[d], reason="Holmes.birth_year has conflicting years", heuristic="timeline"
        )
    )
    assert t == "Holmes’s year of birth has conflicting years"


def test_claim_lines_and_check_names():
    a = _c("Hester Vale", "birthplace", "saltmarsh", 0, "The Harbour", ch="ONE")
    assert where(a.provenance) == "The Harbour · One" and fact(a) == "birthplace: Saltmarsh"
    assert check("slot_mismatch") == "differing detail" and check("new_thing") == "new thing"
