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
