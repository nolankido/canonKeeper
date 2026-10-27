"""Compare two audits of the same series verdict by verdict: what a prompt or model change
actually moved. Findings are matched on (check, subject, predicate, values), so a candidate
that appears in only one run shows as such.

    canonkeeper compare before/report.json after/report.json
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from canonkeeper.models import AuditReport, Finding

Key = tuple[str, str, str, tuple[str, ...]]


def finding_key(f: Finding) -> Key:
    c = f.candidate
    first = c.claims[0]
    values = tuple(sorted({cl.object.lower() for cl in c.claims}))
    return (c.heuristic, first.subject, first.predicate, values)


@dataclass
class Change:
    key: Key
    before: Finding | None
    after: Finding | None

    @property
    def label(self) -> str:
        f = self.after or self.before
        assert f is not None
        from canonkeeper.report.display import headline

        return headline(f.candidate)


def compare(
    before: AuditReport, after: AuditReport
) -> tuple[Counter[str], Counter[str], list[Change]]:
    """Verdict counts for each run, and every finding whose verdict differs or that only one
    run judged."""
    a = {finding_key(f): f for f in before.findings}
    b = {finding_key(f): f for f in after.findings}
    changes = [
        Change(k, a.get(k), b.get(k))
        for k in sorted(set(a) | set(b), key=str)
        if not (k in a and k in b and a[k].verdict == b[k].verdict)
    ]
    return (
        Counter(f.verdict for f in before.findings),
        Counter(f.verdict for f in after.findings),
        changes,
    )
