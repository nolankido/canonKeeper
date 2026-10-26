import json

from canonkeeper.canon import CanonStore
from canonkeeper.extract import Extractor
from canonkeeper.llm import MockLLM
from canonkeeper.models import Claim, ClaimKind, Provenance


def test_extractor_grounds_quotes_and_drops_hallucinations(series, settings):
    def handler(model, messages):
        chunk = messages[-1]["content"].split("<chunk>", 1)[1].split("</chunk>", 1)[0]
        claims = []
        if "Alice Grey has green eyes." in chunk:
            claims.append(
                {
                    "kind": "attribute",
                    "subject": "Alice Grey",
                    "predicate": "eye_color",
                    "object": "green",
                    "quote": "Alice Grey has green eyes.",
                }
            )
        # hallucinated quote: not in the chunk
        claims.append(
            {
                "kind": "attribute",
                "subject": "Alice Grey",
                "predicate": "height",
                "object": "tall",
                "quote": "Alice was very tall indeed.",
            }
        )
        return json.dumps({"claims": claims, "aliases": {"Alice Grey": ["Alice", "Miss Grey"]}})

    ext = Extractor(MockLLM(handler), settings)
    res = ext.extract_series(series, progress=False)
    assert all(c.predicate == "eye_color" for c in res.claims)
    assert ext.stats.dropped_ungrounded > 0
    assert res.aliases["Alice Grey"] == ["Alice", "Miss Grey"]
    c = res.claims[0]
    assert c.provenance.char_start is not None
    book = series.book(c.provenance.book)
    assert (
        book.text[c.provenance.char_start : c.provenance.char_end] == "Alice Grey has green eyes."
    )


def test_store_roundtrip_and_alias_resolution(tmp_path):
    store = CanonStore(tmp_path / "bible.jsonl")
    store.register_aliases({"Sherlock Holmes": ["Holmes", "Mr. Holmes"]})
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Holmes",
            predicate="eye_color",
            object="grey",
            provenance=prov,
        )
    )
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Sherlock Holmes",
            predicate="eye_color",
            object="grey",
            provenance=prov.model_copy(update={"chapter": "II"}),
        )
    )
    store.save()

    loaded = CanonStore.load(tmp_path / "bible.jsonl")
    assert len(loaded) == 2
    assert loaded.canonical("holmes") == "Sherlock Holmes"
    assert loaded.canonical("Mr Holmes") == "Sherlock Holmes"  # fuzzy
    slots = loaded.slots()
    assert ("sherlock holmes", "eye_color") in slots
    assert len(slots[("sherlock holmes", "eye_color")]) == 2
    assert loaded.entities() == ["Sherlock Holmes"]


def test_store_dedupes_identical_claims(tmp_path):
    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    c = Claim(kind=ClaimKind.ATTRIBUTE, subject="A", predicate="p", object="o", provenance=prov)
    assert store.add(c) is True
    assert store.add(c) is False
    assert len(store) == 1


def test_grounder_handles_line_wraps_splices_and_near_misses():
    from canonkeeper.extract.extractor import Grounder

    text = (
        "There I was struck on the shoulder by a Jezail bullet, which\n"
        "shattered the bone and grazed the subclavian artery. I should have\n"
        "fallen into the hands of the murderous Ghazis had it not been for the\n"
        "devotion and courage shown by Murray, my orderly.\n\n"
        "Worn with pain, I was removed to the base hospital at Peshawar."
    )
    g = Grounder(text)
    # exact
    assert g.find("Murray, my orderly")[2] == "Murray, my orderly"
    # the model writes a space where Gutenberg wrapped the line
    a, b, q = g.find("a Jezail bullet, which shattered the bone")
    assert text[a:b] == "a Jezail bullet, which\nshattered the bone"
    assert q == "a Jezail bullet, which shattered the bone"
    # spliced with an ellipsis: the longest verbatim segment is grounded and becomes the quote
    a, b, q = g.find("struck on the shoulder by a Jezail bullet... base hospital at Peshawar")
    assert q == "struck on the shoulder by a Jezail bullet"
    assert text[a:b] == q
    # a near miss (one wrong word) still grounds, and the stored quote is the real text
    hit = g.find("grazed the subclavian vein. I should have fallen")
    assert hit is not None and "artery" in hit[2]
    # garbage does not
    assert g.find("Holmes played the violin badly all afternoon") is None
    assert g.find("") is None


def test_repair_claim_recovers_predicate_as_key_and_empty_death_objects():
    from canonkeeper.extract.extractor import repair_claim

    c = repair_claim(
        {"kind": "possession", "subject": "Drebber", "possession": "gold watch", "quote": "q"}
    )
    assert (
        c["predicate"] == "possession" and c["object"] == "gold watch" and c["kind"] == "attribute"
    )
    c = repair_claim({"kind": "occupation", "subject": "Hope", "object": "cabman", "quote": "q"})
    assert c["predicate"] == "occupation"
    c = repair_claim(
        {"kind": "event", "subject": "Drebber", "predicate": "died", "object": None, "quote": "q"}
    )
    assert c["object"] == "true"
    assert (
        repair_claim(
            {
                "kind": "attribute",
                "subject": "X",
                "predicate": "eye_color",
                "object": None,
                "quote": "q",
            }
        )
        is None
    )
    assert repair_claim({"subject": "X", "quote": "q"}) is None


def test_alive_false_normalises_to_dead_true():
    from canonkeeper.canon.normalize import normalize_claim

    assert normalize_claim("alive", "false") == ("dead", "true")
    assert normalize_claim("dead", "False") == ("alive", "true")
    assert normalize_claim("died", "none") == ("died", "true")
    assert normalize_claim("eye colour", "Gray") == ("eye_color", "grey")
