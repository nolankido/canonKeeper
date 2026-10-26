from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from canonkeeper.eval.inject import Injection
from canonkeeper.models import Finding


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


def _hit(inj: Injection, f: Finding) -> bool:
    subj_ok = any(
        fuzz.token_set_ratio(c.subject.lower(), inj.subject.lower()) >= 85
        for c in f.candidate.claims
    )
    if not subj_ok:
        return False
    if inj.predicate == "dead":
        return f.candidate.heuristic == "dead_speaks" or any(
            c.predicate in {"dead", "died"} for c in f.candidate.claims
        )
    pred_ok = any(c.predicate == inj.predicate for c in f.candidate.claims)
    val_ok = any(
        fuzz.partial_ratio(c.object.lower(), inj.injected.lower()) >= 85 for c in f.candidate.claims
    )
    return pred_ok and val_ok


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
