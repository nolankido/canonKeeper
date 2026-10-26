from __future__ import annotations

from rich.progress import Progress

from canonkeeper.audit import prompts
from canonkeeper.canon import CanonStore
from canonkeeper.config import Settings
from canonkeeper.corpus import Series
from canonkeeper.llm import LLM
from canonkeeper.llm.client import parse_json
from canonkeeper.models import Candidate, Finding, Verdict
from canonkeeper.tokens import count_tokens

_VERDICTS: set[str] = {"contradiction", "deliberate", "ambiguous", "not_conflict"}


class Adjudicator:
    def __init__(self, llm: LLM, settings: Settings, series: Series | None = None) -> None:
        self.llm = llm
        self.settings = settings
        self.series = series

    # --- context assembly ------------------------------------------------------------------
    def _context(self, cand: Candidate) -> str:
        """Chapters the claims came from, then (budget permitting) the whole books. Wonder's
        1M window means a six-book series usually fits; we still cap to leave headroom."""
        if self.series is None:
            return "(no source text available; judge from quotes and bible only)"
        budget = self.settings.adjudicate_context_tokens
        blocks: list[str] = []
        used = 0
        seen: set[tuple[str, int]] = set()
        # Pass 1: the chapters that contain the quotes.
        for c in cand.claims:
            key = (c.provenance.book, c.provenance.chapter_index)
            if key in seen:
                continue
            seen.add(key)
            book = self.series.book(c.provenance.book)
            ch = book.chapters[c.provenance.chapter_index]
            t = count_tokens(ch.text)
            if used + t > budget:
                break
            blocks.append(f"### Book {book.index + 1} — {book.title} — {ch.title}\n{ch.text}")
            used += t
        # Pass 2: everything else in series order, while it fits.
        for book in self.series.books:
            for ch in book.chapters:
                key = (book.key, ch.index)
                if key in seen:
                    continue
                t = count_tokens(ch.text)
                if used + t > budget:
                    return "\n\n".join(blocks)
                seen.add(key)
                blocks.append(f"### Book {book.index + 1} — {book.title} — {ch.title}\n{ch.text}")
                used += t
        return "\n\n".join(blocks)

    def _messages(
        self, cand: Candidate, store: CanonStore, series_name: str
    ) -> list[dict[str, str]]:
        entity = store.canonical(cand.claims[0].subject)
        claims_block = "\n".join(prompts.claim_line(i + 1, c) for i, c in enumerate(cand.claims))
        others = [c for c in store.for_entity(entity) if c.id not in {x.id for x in cand.claims}]
        entity_block = (
            "\n".join(
                f"- {c.predicate} = {c.object}"
                + (f" (at {c.story_time})" if c.story_time else "")
                + f"  [{c.provenance.locator}]"
                for c in others[:80]
            )
            or "(nothing else recorded)"
        )
        user = prompts.USER_TEMPLATE.format(
            series=series_name,
            heuristic=cand.heuristic,
            reason=cand.reason,
            claims_block=claims_block,
            entity=entity,
            entity_block=entity_block,
            context_block=self._context(cand),
        )
        return [{"role": "system", "content": prompts.SYSTEM}, {"role": "user", "content": user}]

    # --- adjudication ----------------------------------------------------------------------
    def judge(self, cand: Candidate, store: CanonStore, series_name: str) -> Finding:
        raw = self.llm.complete(
            self.settings.adjudicate_model,
            self._messages(cand, store, series_name),
            json_mode=True,
        )
        try:
            data = parse_json(raw)
        except Exception:
            data = {}
        verdict = str(data.get("verdict", "ambiguous")).lower()
        if verdict not in _VERDICTS:
            verdict = "ambiguous"
        severity = str(data.get("severity", "medium")).lower()
        if severity not in {"high", "medium", "low"}:
            severity = "medium"
        return Finding(
            candidate=cand,
            verdict=verdict,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            explanation=str(data.get("explanation", "")).strip() or "(no explanation returned)",
            suggested_fix=(
                str(data["suggested_fix"]).strip() if data.get("suggested_fix") else None
            ),
            adjudicator_model=self.settings.adjudicate_model,
        )

    def judge_all(
        self,
        candidates: list[Candidate],
        store: CanonStore,
        series_name: str,
        *,
        progress: bool = True,
    ) -> list[Finding]:
        findings: list[Finding] = []
        cands = candidates[: self.settings.max_candidates_per_run]
        with Progress(disable=not progress) as bar:
            task = bar.add_task("adjudicating", total=len(cands))
            for cand in cands:
                findings.append(self.judge(cand, store, series_name))
                bar.advance(task)
        order: dict[Verdict, int] = {
            "contradiction": 0,
            "ambiguous": 1,
            "deliberate": 2,
            "not_conflict": 3,
        }
        sev = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: (order[f.verdict], sev[f.severity]))
        return findings
