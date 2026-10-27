from canonkeeper.audit import find_candidates
from canonkeeper.audit.candidates import dead_speaks, slot_mismatch
from canonkeeper.eval import inject, score
from canonkeeper.eval.score import finding_key
from canonkeeper.pipeline import audit, build_bible
from canonkeeper.report import write_report


def test_end_to_end_finds_planted_contradictions(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "bible.jsonl", progress=False)
    assert len(store) > 5

    cands = find_candidates(store)
    heuristics = {c.heuristic for c in cands}
    # Alice: green (b1,b2) vs blue (b3)  -> slot_mismatch
    # Carol: dies in b3 ch2, walks in b3 ch3 -> dead_speaks
    assert "slot_mismatch" in heuristics
    assert "dead_speaks" in heuristics
    # location is multi-valued: walking to different towns is not a conflict
    assert not any(c.claims[0].predicate == "location" for c in slot_mismatch(store))

    report = audit(series, store, mock_llm, settings, progress=False)
    assert report.candidates_total == len(cands)
    reasons = " | ".join(f.candidate.reason for f in report.errors)
    assert "Alice Grey.eye_color" in reasons
    assert "Carol Vane dies" in reasons

    html, js = write_report(report, tmp_path / "out")
    body = html.read_text()
    assert "Alice Grey has blue eyes." in body
    assert "Suggested fix" in body
    assert js.exists()


def test_dead_speaks_requires_later_action(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "bible.jsonl", progress=False)
    hits = dead_speaks(store)
    assert len(hits) == 1
    assert hits[0].claims[0].subject == "Carol Vane"
    assert hits[0].claims[1].provenance.chapter_index > hits[0].claims[0].provenance.chapter_index


def test_eval_harness_scores_injected_defects(series, settings, mock_llm, tmp_path):
    clean = build_bible(series, mock_llm, settings, tmp_path / "clean.jsonl", progress=False)
    baseline = audit(series, clean, mock_llm, settings, progress=False)
    baseline_ids = {finding_key(f) for f in baseline.errors}

    iset = inject(series, clean, n=3, seed=1)
    assert 1 <= len(iset.injections) <= 3
    for inj in iset.injections:
        assert inj.sentence in iset.series.book(inj.book).text

    mutated = build_bible(iset.series, mock_llm, settings, tmp_path / "mut.jsonl", progress=False)
    report = audit(iset.series, mutated, mock_llm, settings, progress=False)
    s = score(iset.injections, report.findings, baseline_ids=baseline_ids)
    assert s.n_injected == len(iset.injections)
    # The mock extractor understands the injected templates for eyes/birthplace/death,
    # so everything plantable in this fixture should be recovered.
    assert s.recall == 1.0, (s.missed, [f.candidate.reason for f in report.findings])
    assert "| injected |" in s.markdown()


def test_origin_mismatch_compares_nationality_with_birthplace_by_country(tmp_path):
    from canonkeeper.audit.candidates import origin_mismatch
    from canonkeeper.canon import CanonStore
    from canonkeeper.canon.normalize import same_country
    from canonkeeper.models import Claim, ClaimKind, Provenance

    assert same_country("Welsh", "Cardiff") is True
    assert same_country("an Englishman", "Pershore") is True
    assert same_country("English", "Britain") is True
    assert same_country("German", "Wales") is False
    assert same_country("Martian", "Wales") is None

    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Dr. Becher",
            predicate="nationality",
            object="german",
            provenance=prov,
        )
    )
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Dr. Becher",
            predicate="birthplace",
            object="wales",
            provenance=prov.model_copy(update={"quote": "q2", "book": "02", "book_index": 1}),
        )
    )
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Jonathan Small",
            predicate="nationality",
            object="englishman",
            provenance=prov,
        )
    )
    store.add(
        Claim(
            kind=ClaimKind.ATTRIBUTE,
            subject="Jonathan Small",
            predicate="birthplace",
            object="pershore",
            provenance=prov.model_copy(update={"quote": "q3"}),
        )
    )
    hits = origin_mismatch(store)
    assert len(hits) == 1 and hits[0].claims[0].subject == "Dr. Becher"


def test_role_collision_needs_two_people_in_one_household_role(tmp_path):
    from canonkeeper.audit.candidates import role_collision
    from canonkeeper.canon import CanonStore
    from canonkeeper.models import Claim, ClaimKind, Provenance

    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="02", book_index=1, chapter="I", chapter_index=0, quote="q")

    def add(subj, pred, obj, q):
        store.add(
            Claim(
                kind=ClaimKind.ATTRIBUTE,
                subject=subj,
                predicate=pred,
                object=obj,
                provenance=prov.model_copy(update={"quote": q}),
            )
        )

    add("Mrs. Hudson", "occupation", "landlady", "our landlady, Mrs. Hudson")
    add("Mrs. Hudson", "employed_by", "Sherlock Holmes", "q2")
    add("Mrs. Turner", "occupation", "landlady", "Mrs. Turner has brought in the tray")
    assert role_collision(store) == []  # Turner has no household link yet
    add("Mrs. Turner", "employed_by", "Sherlock Holmes", "q4")
    hits = role_collision(store)
    assert len(hits) == 1 and hits[0].heuristic == "role_collision"
    assert {c.subject for c in hits[0].claims} == {"Mrs. Hudson", "Mrs. Turner"}
    # A different household's landlady is not a collision.
    add("Mrs. Warren", "occupation", "landlady", "q5")
    add("Mrs. Warren", "employed_by", "Mr. Warren", "q6")
    assert len(role_collision(store)) == 1
