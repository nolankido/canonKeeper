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
    assert role_collision(store) == []  # Turner has no household link, Holmes not in scene
    # Weak tie: the known household head has claims in the same chapter as Turner's role.
    add("Sherlock Holmes", "habit", "pipe", "q3")
    hits = role_collision(store)
    assert len(hits) == 1 and "only that Sherlock Holmes is in the scene" in hits[0].reason
    add("Mrs. Turner", "employed_by", "Sherlock Holmes", "q4")
    hits = role_collision(store)
    assert len(hits) == 1 and hits[0].heuristic == "role_collision"
    assert "only that" not in hits[0].reason
    assert {c.subject for c in hits[0].claims} == {"Mrs. Hudson", "Mrs. Turner"}
    # A different household's landlady is not a collision.
    add("Mrs. Warren", "occupation", "landlady", "q5")
    add("Mrs. Warren", "employed_by", "Mr. Warren", "q6")
    assert len(role_collision(store)) == 1


def test_two_parents_are_not_a_contradiction(tmp_path):
    from canonkeeper.audit.candidates import slot_mismatch
    from canonkeeper.canon import CanonStore
    from canonkeeper.models import Claim, ClaimKind, Provenance

    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")
    for i, parent in enumerate(["Grimesby Roylott", "Mrs. Stoner"]):
        store.add(
            Claim(
                kind=ClaimKind.RELATION,
                subject="Helen Stoner",
                predicate="child_of",
                object=parent,
                provenance=prov.model_copy(update={"quote": f"q{i}"}),
            )
        )
    assert slot_mismatch(store) == []
    store.add(
        Claim(
            kind=ClaimKind.RELATION,
            subject="Helen Stoner",
            predicate="child_of",
            object="Dr. Watson",
            provenance=prov.model_copy(update={"quote": "q9"}),
        )
    )
    assert len(slot_mismatch(store)) == 1


def test_died_with_a_victim_is_killed_not_death(tmp_path):
    from canonkeeper.audit.candidates import dead_speaks
    from canonkeeper.canon import CanonStore
    from canonkeeper.models import Claim, ClaimKind, ExtractionResult, Provenance

    store = CanonStore(tmp_path / "b.jsonl")
    prov = Provenance(book="01", book_index=0, chapter="I", chapter_index=0, quote="q")

    def claim(subj, kind, pred, obj, q, chapter=0):
        return Claim(
            kind=kind,
            subject=subj,
            predicate=pred,
            object=obj,
            provenance=prov.model_copy(update={"quote": q, "chapter_index": chapter}),
        )

    store.ingest(
        ExtractionResult(
            claims=[
                claim("Tonga", ClaimKind.ATTRIBUTE, "nationality", "Andaman", "q0"),
                claim("Sherlock Holmes", ClaimKind.EVENT, "died", "Tonga", "q1", 5),
                claim("Sherlock Holmes", ClaimKind.STATE, "location", "Baker Street", "q2", 7),
            ],
            aliases={},
        )
    )
    # Known victim at ingest time: rewritten to `killed`.
    preds = {(c.subject, c.predicate, c.object) for c in store.claims}
    assert ("Sherlock Holmes", "killed", "Tonga") in preds
    assert dead_speaks(store) == []
    # Victim only learned later: the read-time guard still refuses to call it a death.
    store.add(claim("John Watson", ClaimKind.EVENT, "died", "Moriarty", "q3", 5))
    store.add(claim("John Watson", ClaimKind.STATE, "location", "Baker Street", "q4", 7))
    assert [c.reason for c in dead_speaks(store)]  # unknown object: counts as death for now
    store.add(claim("Moriarty", ClaimKind.ATTRIBUTE, "occupation", "professor", "q5"))
    assert dead_speaks(store) == []


def test_rebuild_replaces_claims_instead_of_appending(series, settings, mock_llm, tmp_path):
    """A second build must not duplicate claims, even when the ingest rules changed between
    runs, and a partial build keeps the other books' claims."""
    import json

    from canonkeeper.corpus import Series

    path = tmp_path / "bible.jsonl"
    first = build_bible(series, mock_llm, settings, path, progress=False)
    n = len(first)
    # Simulate a bible written under older ingest rules: a stale, differently-normalised claim
    # for book 1 that today's ingest would never produce.
    rows = path.read_text(encoding="utf-8").splitlines()
    stale = json.loads(rows[1])
    stale["object"] = stale["object"] + " (old normalisation)"
    path.write_text("\n".join([*rows, json.dumps(stale)]) + "\n", encoding="utf-8")

    again = build_bible(series, mock_llm, settings, path, progress=False)
    assert len(again) == n
    assert not any("(old normalisation)" in c.object for c in again.claims)

    only_last = Series(series.name, [series.books[-1]])
    partial = build_bible(only_last, mock_llm, settings, path, progress=False)
    assert len(partial) == n
    assert {c.provenance.book for c in partial.claims} == {b.key for b in series.books}


def _claim(subj, pred, obj, *, book=0, chapter=0, q="q", kind=None):
    from canonkeeper.models import Claim, ClaimKind, Provenance

    return Claim(
        kind=kind or ClaimKind.ATTRIBUTE,
        subject=subj,
        predicate=pred,
        object=obj,
        provenance=Provenance(
            book=f"0{book + 1}",
            book_index=book,
            chapter=f"c{chapter}",
            chapter_index=chapter,
            quote=q,
        ),
    )


def test_landlady_collision_without_any_employment_link(tmp_path):
    """The real Holmes shape: nobody is 'employed_by' Holmes. Mrs. Hudson is the landlady in
    several chapters (an anchor), Mrs. Turner once, both in scenes with Holmes. One-scene
    butlers in houses the detective visits must not collide with anything."""
    from canonkeeper.audit.candidates import role_collision
    from canonkeeper.canon import CanonStore

    st = CanonStore(tmp_path / "b.jsonl")
    for ch in range(6):  # Holmes and Watson are the protagonists: facts everywhere
        st.add(_claim("Sherlock Holmes", "habit", f"pipe {ch}", book=1, chapter=ch, q=f"h{ch}"))
        st.add(_claim("John Watson", "habit", f"notes {ch}", book=1, chapter=ch, q=f"w{ch}"))
    st.add(_claim("Mrs. Hudson", "occupation", "landlady", book=1, chapter=0, q="our landlady"))
    st.add(_claim("Mrs. Hudson", "occupation", "landlady", book=1, chapter=2, q="Mrs. Hudson"))
    st.add(_claim("Mrs. Turner", "occupation", "landlady", book=1, chapter=4, q="Mrs. Turner"))
    st.add(_claim("Brunton", "occupation", "butler", book=1, chapter=1, q="Brunton the butler"))
    st.add(_claim("Barrymore", "occupation", "butler", book=1, chapter=3, q="Barrymore"))
    st.add(_claim("Barrymore", "occupation", "butler", book=1, chapter=5, q="Barrymore 2"))
    st.add(_claim("Steiler", "occupation", "landlord", book=1, chapter=1, q="the landlord"))
    st.add(_claim("Hayes", "occupation", "landlord", book=1, chapter=3, q="Reuben Hayes"))
    hits = role_collision(st)
    assert len(hits) == 1, [h.reason for h in hits]
    assert {c.subject for c in hits[0].claims} == {"Mrs. Hudson", "Mrs. Turner"}


def test_died_naming_a_person_or_creature_is_a_killing(tmp_path):
    from canonkeeper.audit.candidates import dead_speaks
    from canonkeeper.canon import CanonStore
    from canonkeeper.canon.normalize import looks_like_victim
    from canonkeeper.models import ClaimKind, ExtractionResult

    for v in ["islander", "two warders", "the hound", "henry staunton", "husband of mary fraser"]:
        assert looks_like_victim(v), v
    for v in ["reichenbach falls", "utrecht", "1876", "fright", "in a fit", "hanged", "cell"]:
        assert not looks_like_victim(v), v

    st = CanonStore(tmp_path / "b.jsonl")
    ev = ClaimKind.EVENT
    st.ingest(
        ExtractionResult(
            claims=[
                _claim("John Watson", "died", "islander", book=1, chapter=11, q="pistols", kind=ev),
                _claim(
                    "John Watson",
                    "location",
                    "Baker Street",
                    book=2,
                    chapter=0,
                    q="w",
                    kind=ClaimKind.STATE,
                ),
                _claim(
                    "Sherlock Holmes",
                    "died",
                    "reichenbach falls",
                    book=3,
                    chapter=11,
                    q="r",
                    kind=ev,
                ),
                _claim(
                    "Sherlock Holmes",
                    "location",
                    "Dartmoor",
                    book=4,
                    chapter=0,
                    q="d",
                    kind=ClaimKind.STATE,
                ),
            ],
            aliases={},
        )
    )
    assert any(c.predicate == "killed" and c.subject == "John Watson" for c in st.claims)
    hits = dead_speaks(st)
    assert [h.claims[0].subject for h in hits] == ["Sherlock Holmes"]


def test_names_never_fuzzy_merge_across_gender_and_variants_do_merge(tmp_path):
    from canonkeeper.canon import CanonStore
    from canonkeeper.models import ExtractionResult

    st = CanonStore(tmp_path / "b.jsonl")
    st.ingest(
        ExtractionResult(
            claims=[
                _claim("Mr. Turner", "occupation", "landowner", q="a"),
                _claim("Mrs. Turner", "occupation", "landlady", q="b"),
                _claim("Enoch J. Drebber", "nationality", "american", q="c"),
                _claim("Enoch J. Drebber", "occupation", "traveller", q="d"),
                _claim("Enoch Drebber", "dead", "true", q="e"),
                _claim("Young Drebber", "occupation", "elder", q="f"),
            ],
            aliases={"Mr. Turner": ["Turner"]},
        )
    )
    assert {"Mr. Turner", "Mrs. Turner"} <= set(st.entities())
    assert "Enoch Drebber" not in st.entities()
    assert len(st.for_entity("Enoch J. Drebber")) == 3
    assert st.canonical("Enoch Drebber") == "Enoch J. Drebber"
    assert "Young Drebber" in st.entities()


def test_misses_are_attributed_to_the_stage_that_lost_them(series, settings, mock_llm, tmp_path):
    from canonkeeper.eval.score import diagnose, misses_markdown

    clean = build_bible(series, mock_llm, settings, tmp_path / "clean.jsonl", progress=False)
    iset = inject(series, clean, n=3, seed=1)
    mutated = build_bible(iset.series, mock_llm, settings, tmp_path / "mut.jsonl", progress=False)
    report = audit(iset.series, mutated, mock_llm, settings, progress=False)
    inj, hit = score(iset.injections, report.findings).matched[0]

    # Nothing extracted from the planted book → lost at extraction.
    others = [c for c in mutated.claims if c.provenance.book != inj.book]
    assert diagnose([inj], others, report.findings)[0].stage == "extraction"
    # Extracted, but no candidate raised → lost at the screen.
    assert diagnose([inj], mutated.claims, [])[0].stage == "screen"
    # Raised, but the adjudicator cleared it → lost at judgement, with its reason.
    cleared = hit.model_copy(update={"verdict": "deliberate", "explanation": "a disguise"})
    m = diagnose([inj], mutated.claims, [cleared])[0]
    assert m.stage == "judgement" and "a disguise" in m.detail
    assert "| planted | lost at |" in misses_markdown([m])


def test_two_nationalities_are_raised_once(tmp_path):
    from canonkeeper.audit import find_candidates
    from canonkeeper.canon import CanonStore

    st = CanonStore(tmp_path / "b.jsonl")
    st.add(_claim("Dr. Becher", "nationality", "german", q="is Dr. Becher a German"))
    st.add(_claim("Dr. Becher", "nationality", "englishman", book=0, chapter=1, q="an Englishman"))
    hits = find_candidates(st)
    assert [h.heuristic for h in hits] == ["slot_mismatch"]


def test_draft_check_only_pays_to_judge_the_drafts_conflicts(tmp_path):
    """Published-vs-published conflicts are the audit's business; a check must not send them
    to the adjudicator (it used to judge everything, then filter)."""
    from canonkeeper.demo import DRAFT4, write_demo_series
    from canonkeeper.llm import MockLLM
    from canonkeeper.llm.rules import handler
    from canonkeeper.pipeline import check_manuscript

    manifest = write_demo_series(tmp_path / "demo")
    from canonkeeper.cli import _load
    from canonkeeper.config import Settings

    s = Settings(provider="mock", workdir=tmp_path / "wd", cache_dir=tmp_path / "c", _env_file=None)
    _, series = _load(manifest, s)
    store = build_bible(series, MockLLM(handler), s, tmp_path / "b.jsonl", progress=False)
    judged: list[str] = []

    def recording(model, messages):
        if "## Candidate" in messages[-1]["content"]:
            judged.append(messages[-1]["content"])
        return handler(model, messages)

    draft = tmp_path / "04-the-ledger.txt"
    draft.write_text(DRAFT4, encoding="utf-8")
    report = check_manuscript(series, store, draft, MockLLM(recording), s, progress=False)
    assert judged, "the draft's conflicts are judged"
    assert all("04-the-ledger" in block for block in judged)
    assert "Oswin Reed dies" not in "".join(judged)  # a published-vs-published conflict
    assert {f.candidate.claims[0].subject for f in report.errors} == {"Ivy Marlowe", "Tobias Crane"}
