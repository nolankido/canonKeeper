from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from canonkeeper.eval.inject import Injection
from canonkeeper.models import Claim, Finding


@dataclass
class Score:
    n_injected: int
    n_reported: int
    tp: int
    fp: int
    fn: int
    matched: list[tuple[Injection, Finding]] = field(default_factory=list)
    missed: list[Injection] = field(default_factory=list)
    extra: list[Finding] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def markdown(self) -> str:
        return (
            "| injected | reported | TP | FP | FN | precision | recall | F1 |\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
            f"| {self.n_injected} | {self.n_reported} | {self.tp} | {self.fp} | {self.fn} | "
            f"{self.precision:.2f} | {self.recall:.2f} | {self.f1:.2f} |"
        )


_ORIGIN = {"nationality", "birthplace"}


def _subject_ok(inj: Injection, c: Claim) -> bool:
    return fuzz.token_set_ratio(c.subject.lower(), inj.subject.lower()) >= 85


def _claim_hit(inj: Injection, c: Claim) -> bool:
    """`c` asserts the planted value for the planted subject."""
    from canonkeeper.canon.normalize import same_country

    if not _subject_ok(inj, c):
        return False
    if inj.predicate == "dead":
        return c.predicate in {"dead", "died"}
    pred_ok = c.predicate == inj.predicate or (c.predicate in _ORIGIN and inj.predicate in _ORIGIN)
    if not pred_ok:
        return False
    if fuzz.partial_ratio(c.object.lower(), inj.injected.lower()) >= 85:
        return True
    return inj.predicate in _ORIGIN and same_country(c.object, inj.injected) is True


def _hit(inj: Injection, f: Finding) -> bool:
    if not any(_subject_ok(inj, c) for c in f.candidate.claims):
        return False
    if inj.predicate == "dead":
        return f.candidate.heuristic == "dead_speaks" or any(
            c.predicate in {"dead", "died"} for c in f.candidate.claims
        )
    return any(_claim_hit(inj, c) for c in f.candidate.claims)


@dataclass
class Miss:
    """Where the pipeline lost a planted defect."""

    injection: Injection
    stage: str  # "extraction" | "screen" | "judgement" | "baseline"
    detail: str


def diagnose(
    missed: list[Injection],
    claims: list[Claim],
    findings: list[Finding],
    *,
    baseline_ids: set[str] | None = None,
) -> list[Miss]:
    """For each missed defect: was the planted sentence extracted as a fact, did the screen
    raise a conflict on it, and if so what did the adjudicator rule? Recall is only worth
    improving at the stage that actually loses it."""
    baseline_ids = baseline_ids or set()
    out: list[Miss] = []
    for inj in missed:
        planted = [c for c in claims if c.provenance.book == inj.book and _claim_hit(inj, c)]
        if not planted:
            out.append(Miss(inj, "extraction", "no fact was extracted from the planted sentence"))
            continue
        f = next((f for f in findings if _hit(inj, f)), None)
        if f is None:
            c = planted[0]
            out.append(
                Miss(inj, "screen", f"extracted as {c.subject}.{c.predicate} = {c.object!r}")
            )
        elif f.is_error and finding_key(f) in baseline_ids:
            out.append(Miss(inj, "baseline", "reported, but matched a pre-existing contradiction"))
        else:
            out.append(Miss(inj, "judgement", f"ruled {f.verdict}: {f.explanation[:160]}"))
    return out


def misses_markdown(misses: list[Miss]) -> str:
    if not misses:
        return ""
    counts: dict[str, int] = {}
    for m in misses:
        counts[m.stage] = counts.get(m.stage, 0) + 1
    lines = [
        "",
        "Misses by stage: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])),
        "",
        "| planted | lost at | detail |",
        "|---|---|---|",
    ]
    for m in misses:
        i = m.injection
        what = f"{i.subject}.{i.predicate} → {i.injected!r} ({i.book})"
        lines.append(f"| {what} | {m.stage} | {m.detail.replace('|', '/')} |")
    return "\n".join(lines)


def score(
    injections: list[Injection], findings: list[Finding], *, baseline_ids: set[str] | None = None
) -> Score:
    """Score contradiction-verdict findings against injected ground truth.

    `baseline_ids` are finding keys from a clean (uninjected) run: genuine pre-existing
    contradictions in the source. They are excluded from the FP count — the tool is not wrong
    for finding Watson's wandering wound in Conan Doyle.
    """
    reported = [f for f in findings if f.is_error]
    baseline_ids = baseline_ids or set()
    reported = [f for f in reported if finding_key(f) not in baseline_ids]
    s = Score(n_injected=len(injections), n_reported=len(reported), tp=0, fp=0, fn=0)
    taken: set[int] = set()
    for inj in injections:
        for i, f in enumerate(reported):
            if i in taken:
                continue
            if _hit(inj, f):
                taken.add(i)
                s.tp += 1
                s.matched.append((inj, f))
                break
        else:
            s.fn += 1
            s.missed.append(inj)
    s.extra = [f for i, f in enumerate(reported) if i not in taken]
    s.fp = len(s.extra)
    return s


def finding_key(f: Finding) -> str:
    """Slot plus its set of disputed values. A planted value turning up in a slot that already
    had a pre-existing contradiction (Watson's wound) changes the key, so it still scores."""
    c = f.candidate.claims[0]
    values = "|".join(sorted({x.object.lower() for x in f.candidate.claims}))
    return f"{c.subject.lower()}|{c.predicate}|{values}"
