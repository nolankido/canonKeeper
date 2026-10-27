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


def test_store_index_follows_late_aliases(tmp_path):
    """Aliases learned after claims were added must re-resolve earlier subjects, and the
    memoised canonical() must not serve a stale answer."""
    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    mk = lambda s, o: Claim(  # noqa: E731
        kind=ClaimKind.ATTRIBUTE, subject=s, predicate="eye_color", object=o, provenance=prov
    )
    store.add(mk("Watson", "blue"))
    assert store.entities() == ["Watson"]
    assert store.for_entity("Watson") and store.canonical("Watson") == "Watson"
    store.register_aliases({"John Watson": ["Watson", "Dr. Watson"]})
    store.add(mk("Dr. Watson", "brown"))
    assert store.canonical("Watson") == "John Watson"
    assert store.entities() == ["John Watson"]
    assert len(store.for_entity("Watson")) == 2
    assert len(store.slots()[("john watson", "eye_color")]) == 2


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


def test_parse_json_tolerates_fences_prose_trailing_commas():
    from canonkeeper.llm.client import parse_json

    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here it is: {"a": [1, 2,], "b": {"c": 3,},}') == {
        "a": [1, 2],
        "b": {"c": 3},
    }
    assert parse_json('{"q": "line\nbreak"}') == {"q": "line\nbreak"}
    import pytest

    with pytest.raises(ValueError):
        parse_json("{'single': 'quotes'")


def test_extractor_repairs_bad_json_via_model_and_skips_hopeless_chunks(series, settings):
    calls = {"n": 0}

    def handler(model, messages):
        calls["n"] += 1
        content = messages[-1]["content"]
        if content.startswith("The following was supposed to be"):
            return '{"claims": [], "aliases": {}}'  # the repair call
        if "Alice Grey has green eyes." in content:
            return "{'claims': broken"  # malformed → repaired
        return "total garbage"  # malformed, and the repair handler returns garbage too? no: repair returns valid

    ext = Extractor(MockLLM(handler), settings)
    ext.extract_series(series, progress=False)
    assert ext.stats.json_repaired >= 1
    assert ext.stats.json_failed == 0
    assert ext.stats.chunks > 0


def test_generic_names_are_neither_aliases_nor_subjects(tmp_path):
    from canonkeeper.canon.normalize import is_generic_name
    from canonkeeper.models import ExtractionResult

    assert is_generic_name("he") and is_generic_name("my old companion")
    assert is_generic_name("the landlady") and is_generic_name("Stranger")
    assert not is_generic_name("Mrs. Hudson") and not is_generic_name("the sleuth-hound")

    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    mk = lambda s, q: Claim(  # noqa: E731
        kind=ClaimKind.ATTRIBUTE,
        subject=s,
        predicate="eye_color",
        object="grey",
        provenance=prov.model_copy(update={"quote": q}),
    )
    store.ingest(
        ExtractionResult(
            claims=[mk("Sherlock Holmes", "q1"), mk("he", "q2"), mk("the landlady", "q3")],
            aliases={"Sherlock Holmes": ["he", "my friend", "Holmes"], "I": ["me"]},
        )
    )
    assert store.aliases == {"Sherlock Holmes": ["Holmes"]}
    assert store.canonical("he") == "he"  # not routed anywhere
    assert [c.subject for c in store.claims] == ["Sherlock Holmes"]
    assert store.dropped_generic == 2


def test_alias_table_is_order_independent_and_survives_reload(tmp_path):
    """ "Dr. Mortimer" arrives both as an entity of its own and as James Mortimer's alias, in
    either order. Both orders, and a save/load round trip, must give one entity."""
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")

    def mk(subj, obj, q):
        return Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject=subj,
            predicate="occupation",
            object=obj,
            provenance=prov.model_copy(update={"quote": q}),
        )

    from canonkeeper.models import ExtractionResult

    claims = [mk("Dr. Mortimer", "doctor", "a"), mk("Mortimer", "surgeon", "b")]
    orders = [
        {"Dr. Mortimer": ["Mortimer"], "James Mortimer": ["Dr. Mortimer"]},
        {"James Mortimer": ["Dr. Mortimer"], "Dr. Mortimer": ["Mortimer"]},
    ]
    for i, aliases in enumerate(orders):
        st = CanonStore(tmp_path / f"b{i}.jsonl")
        st.ingest(ExtractionResult(claims=claims, aliases=aliases))
        assert st.entities() == ["James Mortimer"], (i, st.entities())
        st.save()
        back = CanonStore.load(tmp_path / f"b{i}.jsonl")
        assert back.entities() == ["James Mortimer"]
        assert {c.subject for c in back.claims} == {"James Mortimer"}


def test_salience_hints_find_fact_sentences_and_stay_off_by_default(series, settings):
    from canonkeeper.extract.extractor import Extractor
    from canonkeeper.extract.salience import hints_block, salient_sentences

    text = (
        "Holmes lit his pipe. I had found her with the brown eyes that had first drawn me to "
        "her fixed upon the fire. It was raining. He was a man of barely five feet and a half. "
        '"I was born in Edinburgh," said he. The cab rattled on.'
    )
    got = salient_sentences(text)
    assert len(got) == 3 and "brown eyes" in got[0] and "five feet" in got[1]
    assert "born in Edinburgh" in got[2]
    assert hints_block("It was raining. The cab rattled on.") == ""

    book = series.books[0]
    ch = book.chapters[0]
    plain = Extractor.messages_for("S", book, ch, text, "", "sys")
    hinted = Extractor.messages_for("S", book, ch, text, "", "sys", hints=True)
    assert "biographical fact" not in plain[1]["content"]  # cache keys unchanged when off
    assert hinted[1]["content"].startswith(plain[1]["content"]) and "- " in hinted[1]["content"]
    assert settings.extract_hints is False
    assert not settings.hints_for(sweep_pass=True) and not settings.hints_for(sweep_pass=False)
    sweep_only = settings.model_copy(update={"extract_hint_passes": "sweep"})
    assert sweep_only.hints_for(sweep_pass=True) and not sweep_only.hints_for(sweep_pass=False)
    both = settings.model_copy(update={"extract_hints": True})
    assert both.hints_for(sweep_pass=True) and both.hints_for(sweep_pass=False)


def test_parallel_extraction_gives_the_same_bible_as_sequential(series, settings):
    """Requests overlap and finish out of order; results are merged in submission order, so
    claims, aliases and counts must match a one-at-a-time run exactly."""
    import random
    import time

    from canonkeeper.llm.rules import handler as rules

    def jittery(model, messages):
        time.sleep(random.uniform(0, 0.02))
        return rules(model, messages)

    out = {}
    for n in (1, 4):
        s = settings.model_copy(update={"extract_concurrency": n})
        ext = Extractor(MockLLM(jittery), s)
        res = ext.extract_series(series, progress=False)
        out[n] = (
            [(c.subject, c.predicate, c.object, c.provenance.char_start) for c in res.claims],
            res.aliases,
            vars(ext.stats),
        )
    assert out[1] == out[4] and out[1][0]


def test_a_quota_error_in_one_request_stops_parallel_extraction(series, settings):
    import pytest

    from canonkeeper.llm.client import DailyQuotaExhausted

    calls = {"n": 0}

    def exhausted_after_two(model, messages):
        calls["n"] += 1
        if calls["n"] > 2:
            raise DailyQuotaExhausted(model, 3600)
        return '{"claims": [], "aliases": {}}'

    s = settings.model_copy(update={"extract_concurrency": 3})
    with pytest.raises(DailyQuotaExhausted):
        Extractor(MockLLM(exhausted_after_two), s).extract_series(series, progress=False)


def test_names_spoken_in_dialogue_are_pointed_out_only_when_asked(series, settings):
    """T12: a first name given only in speech (a wife's "James") is listed for the extractor
    with the rule for whose name it is; off by default, so no cached request changes."""
    from canonkeeper.extract import prompts
    from canonkeeper.extract.extractor import Extractor
    from canonkeeper.extract.salience import speech_name_sentences, speech_names_block
    from canonkeeper.llm.rules import extract

    text = (
        '"Or should you rather that I sent James off to bed?" "Come in, Edward!" she said. '
        '"Well, I never," said he. "Yes," said Holmes. "We walked down Baker Street," said '
        'Mr. Hopkins. "Mr. Holmes, sit down." He walked on.'
    )
    got = speech_name_sentences(text)
    assert len(got) == 2 and "sent James off" in got[0] and "Edward" in got[1]
    assert speech_names_block('"Well, then," said he. "Yes, sir."') == ""

    book, ch = series.books[0], series.books[0].chapters[0]
    plain = Extractor.messages_for("S", book, ch, text, "", "sys")
    named = Extractor.messages_for("S", book, ch, text, "", "sys", speech_names=True)
    assert "inside speech" not in plain[1]["content"]
    assert named[1]["content"].startswith(plain[1]["content"]) and "James" in named[1]["content"]

    assert settings.extract_speech_names is False
    on = settings.model_copy(update={"extract_speech_names": True})
    ex = Extractor(None, on)  # type: ignore[arg-type]
    assert ex._addons(prompts.SWEEP_SYSTEM)["speech_names"] is True  # the last pass only
    assert ex._addons(prompts.SYSTEM)["speech_names"] is False
    merged = Extractor(None, on.model_copy(update={"extract_mode": "merged"}))  # type: ignore[arg-type]
    assert merged._addons(prompts.MERGED_SYSTEM)["speech_names"] is True
    assert Extractor(None, settings)._addons(prompts.SWEEP_SYSTEM) == {  # type: ignore[arg-type]
        "hints": False,
        "speech_names": False,
    }

    # The mock reads the same shape, so offline runs and the demo cover it.
    claims = extract('"James," said Mary Watson to her husband, John Watson.')["claims"]
    assert [(c["subject"], c["predicate"], c["object"]) for c in claims] == [
        ("John Watson", "first_name", "James")
    ]


def test_two_names_that_alias_each_other_resolve_to_one_entity_either_way(tmp_path):
    """Regression: one chunk said "Baskerville" is Sir Henry Baskerville, another the reverse.
    Each end resolved to the other, and Sir Henry's 54 facts were filed under a name nothing
    looked up: missing from the bible pages, the explorer and the CSV."""
    from canonkeeper.canon import CanonStore

    s = CanonStore(tmp_path / "bible.jsonl")
    s.register_aliases({"Sir Henry Baskerville": ["Baskerville", "Sir Henry"]})
    s.register_aliases({"Baskerville": ["Sir Henry Baskerville"]})
    for name in ("Baskerville", "Sir Henry Baskerville", "Sir Henry"):
        assert s.canonical(name) == "Sir Henry Baskerville"


def test_an_alias_given_as_a_string_is_one_name_not_its_letters(tmp_path):
    """Regression: {"Sir Henry": "Sir Henry Baskerville"} registered S, r, H, e, n, y."""
    from canonkeeper.canon import CanonStore

    s = CanonStore(tmp_path / "bible.jsonl")
    s.register_aliases({"Sir Henry": "Sir Henry Baskerville"})  # type: ignore[dict-item]
    assert s.aliases["Sir Henry"] == ["Sir Henry Baskerville"]
    assert s.canonical("e") == "e"


def test_a_model_reply_with_a_string_alias_keeps_it_whole(settings):
    from canonkeeper.extract.extractor import Extractor

    class OneReply:
        def complete(self, *a, **k):
            return '{"aliases": {"Sir Henry": "Sir Henry Baskerville"}, "claims": []}'

    from canonkeeper.corpus.chapterize import chapterize
    from canonkeeper.corpus.loaders import Book

    text = "Chapter 1\n\nSir Henry came in."
    book = Book("01", "One", 0, text, chapterize(text))
    r = Extractor(OneReply(), settings).extract_chunk("S", book, book.chapters[0], text, 0, "")  # type: ignore[arg-type]
    assert r.aliases == {"Sir Henry": ["Sir Henry Baskerville"]}
