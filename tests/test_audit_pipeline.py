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
    assert "alice grey.eye_color" in reasons
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
