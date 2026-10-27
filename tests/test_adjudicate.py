import json

from canonkeeper.audit import find_candidates
from canonkeeper.audit.adjudicate import Adjudicator
from canonkeeper.audit.candidates import name_drift
from canonkeeper.llm import MockLLM
from canonkeeper.pipeline import build_bible


def test_batches_share_one_context_and_parse_by_id(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    cands = find_candidates(store)
    assert len(cands) >= 3

    settings.adjudicate_batch_size = 2
    recorder = MockLLM()  # default rule-based handler
    adj = Adjudicator(recorder, settings, series)
    findings = adj.judge_all(cands, store, series.name, progress=False)

    assert len(findings) == len(cands)
    expected_calls = -(-len(cands) // 2)
    assert len(recorder.calls) == expected_calls
    # Every call carries the identical series-context message as user #1 (cacheable prefix),
    # and only the final user message varies.
    contexts = {msgs[1]["content"] for _, msgs in recorder.calls}
    assert len(contexts) == 1
    assert "### Book 1" in next(iter(contexts))
    assert all("## Candidate 1" in msgs[2]["content"] for _, msgs in recorder.calls)


def _passage(messages) -> str:
    """A real passage from the series text the adjudicator was shown (the longest line)."""
    return max(messages[1]["content"].splitlines(), key=len)


def test_missing_or_malformed_verdicts_degrade_to_ambiguous(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    cands = find_candidates(store)[:3]

    def handler(model, messages):
        # Only answers candidate 2, with junk for the rest.
        ev = _passage(messages)
        return json.dumps(
            {
                "verdicts": [
                    {"id": 2, "verdict": "deliberate", "severity": "weird", "evidence": ev},
                    "garbage",
                ]
            }
        )

    settings.adjudicate_batch_size = 10
    adj = Adjudicator(MockLLM(handler), settings, series)
    findings = adj.judge_batch(cands, store, series.name)
    assert [f.verdict for f in findings] == ["ambiguous", "deliberate", "ambiguous"]
    assert findings[1].severity == "medium"  # invalid severity falls back
    assert findings[0].explanation == "(no explanation returned)"


def test_context_budget_prioritises_batch_chapters(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    cands = find_candidates(store)
    dead = next(c for c in cands if c.heuristic == "dead_speaks")  # book 3 chapters

    settings.adjudicate_context_tokens = 120  # room for roughly one short chapter
    adj = Adjudicator(MockLLM(), settings, series)
    full = adj.series_context()
    assert full is not None and not full.complete

    plan = adj.context_for([dead])
    assert plan is not None
    assert plan.chapters_included >= 1
    assert "Book 3" in plan.text  # the dead-speaks chapters won priority over book 1


def test_name_drift_is_its_own_heuristic(series, settings, mock_llm, tmp_path):
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    hits = name_drift(store)
    assert len(hits) == 1
    assert hits[0].claims[0].subject == "Bob Stone"
    assert {c.object for c in hits[0].claims} == {"Robert", "Bob"}
    assert not any(
        c.heuristic == "slot_mismatch" and c.claims[0].predicate == "first_name"
        for c in find_candidates(store)
    )


def test_candidates_missing_from_a_batch_verdict_are_re_asked_alone(
    series, settings, mock_llm, tmp_path
):
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    cands = find_candidates(store)[:3]
    calls: list[int] = []

    def handler(model, messages):
        n = messages[-1]["content"].count("## Candidate ")
        calls.append(n)
        if n > 1:  # the batch answer omits candidate 2
            ev = _passage(messages)
            return json.dumps(
                {
                    "verdicts": [
                        {"id": 1, "verdict": "not_conflict", "evidence": ev},
                        {"id": 3, "verdict": "deliberate", "evidence": ev},
                    ]
                }
            )
        return json.dumps(
            {
                "verdicts": [
                    {
                        "id": 1,
                        "verdict": "contradiction",
                        "severity": "low",
                        "explanation": "re-asked",
                    }
                ]
            }
        )

    settings.adjudicate_batch_size = 10
    adj = Adjudicator(MockLLM(handler), settings, series)
    findings = adj.judge_batch(cands, store, series.name)
    assert [f.verdict for f in findings] == ["not_conflict", "contradiction", "deliberate"]
    assert findings[1].explanation == "re-asked"
    assert calls == [3, 1]


def test_clearing_a_conflict_needs_a_passage_from_the_text(series, settings, mock_llm, tmp_path):
    """Provenance for verdicts: 'deliberate' needs an explaining passage that is really in the
    text and is more than the conflicting quotes; 'not_conflict' needs a real passage."""
    store = build_bible(series, mock_llm, settings, tmp_path / "b.jsonl", progress=False)
    cand = find_candidates(store)[0]
    own_quote = cand.claims[1].provenance.quote

    def run(entry):
        def handler(model, messages):
            return json.dumps({"verdicts": [{"id": 1, **entry(messages)}]})

        return Adjudicator(MockLLM(handler), settings, series).judge_batch(
            [cand], store, series.name
        )[0]

    invented = run(lambda m: {"verdict": "deliberate", "evidence": "It was all a dream."})
    assert invented.verdict == "ambiguous" and "Needs your judgement" in invented.explanation
    bare = run(lambda m: {"verdict": "deliberate", "explanation": "character development"})
    assert bare.verdict == "ambiguous" and "character development" in bare.explanation
    own = run(lambda m: {"verdict": "deliberate", "evidence": own_quote})
    assert own.verdict == "ambiguous"  # the conflicting quote itself explains nothing
    explained = run(lambda m: {"verdict": "deliberate", "evidence": _passage(m)})
    assert explained.verdict == "deliberate" and explained.evidence
    misread = run(lambda m: {"verdict": "not_conflict", "evidence": own_quote})
    assert misread.verdict == "not_conflict"
    real = run(lambda m: {"verdict": "contradiction"})
    assert real.verdict == "contradiction"  # contradictions need no extra proof
