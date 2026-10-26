from canonkeeper.canon import CanonStore
from canonkeeper.canon.normalize import normalize_predicate, normalize_value
from canonkeeper.models import Claim, ClaimKind, ExtractionResult, Provenance


def test_predicate_synonyms_collapse():
    assert normalize_predicate("Eye Colour") == "eye_color"
    assert normalize_predicate("eyes") == "eye_color"
    assert normalize_predicate("born-in") == "birthplace"
    assert normalize_predicate("wounded_in") == "wound_location"
    assert normalize_predicate("unknown_thing") == "unknown_thing"


def test_value_normalisation():
    assert normalize_value("Gray", "eye_color") == "grey"
    assert normalize_value("a grey", "eye_color") == "grey"
    assert normalize_value("the left shoulder.", "wound_location") == "left shoulder"
    assert normalize_value("30 years old", "age") == "30"
    assert normalize_value("Deceased", "dead") == "true"
    # names and relation targets keep their case
    assert normalize_value("John H. Watson", "full_name") == "John H. Watson"
    assert normalize_value("Mary Morstan", "married_to") == "Mary Morstan"


def test_ingest_applies_normalisation_so_slots_merge(tmp_path):
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q1")
    r = ExtractionResult(
        claims=[
            Claim(
                kind=ClaimKind.ATTRIBUTE,
                subject="Holmes",
                predicate="eye colour",
                object="Grey",
                provenance=prov,
            ),
            Claim(
                kind=ClaimKind.ATTRIBUTE,
                subject="Holmes",
                predicate="eyes",
                object="gray",
                provenance=prov.model_copy(update={"quote": "q2"}),
            ),
        ],
        aliases={},
    )
    store = CanonStore(tmp_path / "b.jsonl")
    store.ingest(r)
    slots = store.slots()
    assert list(slots) == [("holmes", "eye_color")]
    assert {c.object for c in slots[("holmes", "eye_color")]} == {"grey"}
