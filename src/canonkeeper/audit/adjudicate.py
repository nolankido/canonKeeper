"""Candidate conflicts → verdicts, in batches, with the series text as a cacheable prefix."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.progress import Progress

from canonkeeper.audit import prompts
from canonkeeper.canon import CanonStore
from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.extract.extractor import Grounder
from canonkeeper.llm import LLM
from canonkeeper.llm.client import parse_json
from canonkeeper.models import Candidate, Finding, Verdict
from canonkeeper.tokens import count_tokens

_VERDICTS: set[str] = {"contradiction", "deliberate", "ambiguous", "not_conflict"}
_SEVERITIES: set[str] = {"high", "medium", "low"}


def _ws(text: str) -> str:
    return " ".join(text.split()).lower()


@dataclass
class ContextPlan:
    text: str
    tokens: int
    chapters_included: int
    chapters_total: int

    @property
    def complete(self) -> bool:
        return self.chapters_included == self.chapters_total


class Adjudicator:
    def __init__(self, llm: LLM, settings: Settings, series: Series | None = None) -> None:
        self.llm = llm
        self.settings = settings
        self.series = series
        self._chapter_tokens: dict[tuple[str, int], int] = {}
        self._series_plan: ContextPlan | None = None

    # --- context assembly ------------------------------------------------------------------
    def _tokens(self, book_key: str, ch_index: int, text: str) -> int:
        key = (book_key, ch_index)
        if key not in self._chapter_tokens:
            self._chapter_tokens[key] = count_tokens(text)
        return self._chapter_tokens[key]

    def _assemble(self, priority: list[tuple[str, int]]) -> ContextPlan:
        """Greedy fill: `priority` chapters first, then the series in order, within budget."""
        assert self.series is not None
        budget = self.settings.adjudicate_context_tokens
        order: list[tuple[str, int]] = list(priority)
        for book in self.series.books:
            for ch in book.chapters:
                if (book.key, ch.index) not in order:
                    order.append((book.key, ch.index))
        total = sum(len(b.chapters) for b in self.series.books)
        chosen: set[tuple[str, int]] = set()
        used = 0
        for key in order:
            book = self.series.book(key[0])
            ch = book.chapters[key[1]]
            t = self._tokens(book.key, ch.index, ch.text)
            if used + t > budget:
                if key in priority:
                    continue  # try to fit the remaining priority chapters at least
                break
            chosen.add(key)
            used += t
        # Emit in reading order regardless of selection order, so the prefix is stable.
        blocks: list[str] = []
        for book in self.series.books:
            for ch in book.chapters:
                if (book.key, ch.index) in chosen:
                    blocks.append(
                        f"### Book {book.index + 1} — {book.title} — {ch.title}\n{ch.text}"
                    )
        return ContextPlan("\n\n".join(blocks), used, len(chosen), total)

    def series_context(self) -> ContextPlan | None:
        """Whole-series context when it fits the budget. Identical for every batch → cacheable."""
        if self.series is None:
            return None
        if self._series_plan is None:
            self._series_plan = self._assemble([])
        return self._series_plan

    def context_for(self, batch: list[Candidate]) -> ContextPlan | None:
        """Whole series if it fits; otherwise the batch's own chapters first, then the rest."""
        plan = self.series_context()
        if plan is None or plan.complete:
            return plan
        priority: list[tuple[str, int]] = []
        for cand in batch:
            for c in cand.claims:
                key = (c.provenance.book, c.provenance.chapter_index)
                if key not in priority:
                    priority.append(key)
        return self._assemble(priority)

    # --- prompt building -------------------------------------------------------------------
    def _candidate_block(self, idx: int, cand: Candidate, store: CanonStore) -> str:
        entity = store.canonical(cand.claims[0].subject)
        claims_block = "\n".join(prompts.claim_line(i + 1, c) for i, c in enumerate(cand.claims))
        ids = {x.id for x in cand.claims}
        others = [c for c in store.for_entity(entity) if c.id not in ids]
        entity_block = (
            "\n".join(
                f"- {c.predicate} = {c.object}"
                + (f" (at {c.story_time})" if c.story_time else "")
                + f"  [{c.provenance.locator}]"
                for c in others[:60]
            )
            or "(nothing else recorded)"
        )
        return prompts.CANDIDATE_TEMPLATE.format(
            id=idx,
            heuristic=cand.heuristic,
            reason=cand.reason,
            claims_block=claims_block,
            entity=entity,
            entity_block=entity_block,
        )

    def messages_for(
        self, batch: list[Candidate], store: CanonStore, series_name: str
    ) -> list[dict[str, str]]:
        plan = self.context_for(batch)
        context_block = plan.text if plan and plan.text else prompts.NO_CONTEXT
        context_msg = prompts.CONTEXT_TEMPLATE.format(
            series=series_name, context_block=context_block
        )
        candidates_block = "\n".join(
            self._candidate_block(i + 1, cand, store) for i, cand in enumerate(batch)
        )
        return [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": context_msg},
            {
                "role": "user",
                "content": prompts.BATCH_TEMPLATE.format(candidates_block=candidates_block),
            },
        ]

    # --- adjudication ----------------------------------------------------------------------
    @staticmethod
    def _finding(cand: Candidate, data: dict, model: str) -> Finding:
        verdict = str(data.get("verdict", "ambiguous")).lower().strip()
        if verdict not in _VERDICTS:
            verdict = "ambiguous"
        severity = str(data.get("severity", "medium")).lower().strip()
        if severity not in _SEVERITIES:
            severity = "medium"
        fix = data.get("suggested_fix")
        ev = data.get("evidence")
        return Finding(
            candidate=cand,
            verdict=verdict,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            explanation=str(data.get("explanation", "")).strip() or "(no explanation returned)",
            suggested_fix=(str(fix).strip() if fix and str(fix).lower() != "null" else None),
            evidence=(str(ev).strip() if ev and str(ev).lower() != "null" else None),
            adjudicator_model=model,
        )

    @staticmethod
    def _require_evidence(f: Finding, shown: Grounder) -> Finding:
        """Provenance for verdicts too: a conflict is only cleared (deliberate / not_conflict)
        on a passage that really occurs in the text the adjudicator was shown, and a
        'deliberate' needs a passage beyond the conflicting quotes themselves. Otherwise the
        finding becomes 'ambiguous' and goes to the author, instead of being dismissed on a
        rationale the text does not support (observed: a 4B local model ruled planted eye-
        colour and birthplace slips 'deliberate, likely character development')."""
        if f.verdict not in ("deliberate", "not_conflict"):
            return f
        own = [_ws(c.provenance.quote) for c in f.candidate.claims]
        hit = shown.find(f.evidence) if f.evidence else None
        if hit is not None:
            passage = _ws(hit[2])
            if f.verdict == "not_conflict" or not any(passage in q for q in own):
                return f.model_copy(update={"evidence": hit[2]})
        elif f.verdict == "not_conflict" and f.evidence:
            # A misread fact is shown by its own sentence, which is verbatim in the bible even
            # when that chapter did not fit in the context window.
            ev = _ws(f.evidence)
            if len(ev) >= 12 and any(ev in q or q in ev for q in own):
                return f
        why = "quoted no passage" if f.evidence is None else "quoted nothing"
        return f.model_copy(
            update={
                "verdict": "ambiguous",
                "evidence": None,
                "explanation": (
                    f"Needs your judgement: the adjudicator ruled this {f.verdict} but {why} "
                    "from the text that shows it. Its reasoning: " + f.explanation
                ),
            }
        )

    def judge_batch(
        self, batch: list[Candidate], store: CanonStore, series_name: str, *, _retry: bool = True
    ) -> list[Finding]:
        """Judge a batch; any candidate the model leaves out of its verdict list is re-asked
        on its own (one small call) rather than silently marked ambiguous."""
        model = self.settings.adjudicate_model
        messages = self.messages_for(batch, store, series_name)
        raw = self.llm.complete(model, messages, json_mode=True)
        # Evidence must come from the series text (message #1), never from the candidate list
        # we wrote ourselves: a 4B model once 'quoted' our own "Book 3 … quote: …" line.
        shown = Grounder(messages[1]["content"])
        try:
            data = parse_json(raw)
        except Exception:
            data = {}
        by_id: dict[int, dict] = {}
        entries = data.get("verdicts") if isinstance(data, dict) else None
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    try:
                        by_id[int(e.get("id", 0))] = e
                    except (TypeError, ValueError):
                        continue
        elif isinstance(data, dict) and len(batch) == 1 and "verdict" in data:
            by_id[1] = data  # tolerate a bare single-verdict object
        findings: list[Finding] = []
        for i, cand in enumerate(batch):
            entry = by_id.get(i + 1)
            if entry is None and _retry and len(batch) > 1:
                findings.append(self.judge_batch([cand], store, series_name, _retry=False)[0])
            else:
                findings.append(
                    self._require_evidence(self._finding(cand, entry or {}, model), shown)
                )
        return findings

    def judge(self, cand: Candidate, store: CanonStore, series_name: str) -> Finding:
        return self.judge_batch([cand], store, series_name)[0]

    def judge_all(
        self,
        candidates: list[Candidate],
        store: CanonStore,
        series_name: str,
        *,
        progress: bool = True,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        cands = candidates[: self.settings.max_candidates_per_run]
        size = max(1, self.settings.adjudicate_batch_size)
        batches = [cands[i : i + size] for i in range(0, len(cands), size)]
        if on_progress:
            on_progress("judging", 0, len(cands))
        with Progress(disable=not progress) as bar:
            task = bar.add_task("adjudicating", total=len(cands))
            for batch in batches:
                findings.extend(self.judge_batch(batch, store, series_name))
                bar.advance(task, len(batch))
                if on_progress:
                    on_progress("judging", len(findings), len(cands))
        order: dict[Verdict, int] = {
            "contradiction": 0,
            "ambiguous": 1,
            "deliberate": 2,
            "not_conflict": 3,
        }
        sev = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: (order[f.verdict], sev[f.severity]))
        return findings
