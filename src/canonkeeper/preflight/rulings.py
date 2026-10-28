"""The author's rulings on what the model left to them.

An audit sorts every candidate into a contradiction, a cleared item, or "needs your judgement":
two passages the model could not call either way (a nickname or a new title, a time that may have
passed). Only the author knows, so the author rules: "error" (a real slip to fix in the next
book) or "fine" (not a problem). A ruling costs nothing, can be changed, and is written onto the
report beside the model's note, so anyone the report is shared with sees both.

Rulings and the guarantee's "marked wrong" are both kept on the job and both drawn onto the
report by `annotate_report`, the one place the report is re-rendered after the audit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from canonkeeper.models import AuditReport, AuthorReview
from canonkeeper.preflight.jobs import Job, JobStore

RULINGS = {"error": "a real error to fix", "fine": "not a problem"}


def load_report(store: JobStore, job_id: str) -> AuditReport | None:
    p = store.dir(job_id) / "reports" / "report.json"
    try:
        return AuditReport.model_validate_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def annotate_report(job: Job, store: JobStore) -> None:
    """Re-render the job's report with every mark and ruling the author made, and a link back to
    the job page. Call with the job's lock held."""
    from canonkeeper.report import write_report

    report = load_report(store, job.id)
    if report is None:
        return
    for g in job.guarantee:
        i = int(g["finding"])
        if 0 <= i < len(report.findings):
            report.findings[i].author_review = AuthorReview(verdict="wrong", note=g.get("note", ""))
    for r in job.rulings:
        i = int(r["finding"])
        if 0 <= i < len(report.findings):
            report.findings[i].author_review = AuthorReview(
                verdict=r["verdict"],  # type: ignore[arg-type]
                note=r.get("note", ""),
            )
    from canonkeeper.preflight.jobs import back_label_for

    write_report(
        report,
        store.dir(job.id) / "reports",
        back_url=f"/jobs/{job.id}",
        back_label=back_label_for(job),
        customer=True,
        actions_url=f"/jobs/{job.id}/findings",
    )


def rule_on_finding(
    store: JobStore, job_id: str, index: int, verdict: str, note: str = ""
) -> str | None:
    """Record the author's ruling on a "needs your judgement" item (replacing an earlier one) and
    re-render the report. Returns why it was refused, or None."""
    if verdict not in RULINGS:
        return f"a ruling is one of: {', '.join(RULINGS)}"
    with store.lock(job_id):
        job = store.get(job_id)
        if job is None:
            return "unknown job"
        if job.status != "done":
            return "only a finished audit's findings can be ruled on"
        report = load_report(store, job_id)
        if (
            report is None
            or not 0 <= index < len(report.findings)
            or report.findings[index].verdict != "ambiguous"
        ):
            return "no such item needing your judgement in this report"
        ruling = {
            "finding": str(index),
            "verdict": verdict,
            "note": note.strip()[:500],
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        rulings = [r for r in job.rulings if r["finding"] != str(index)] + [ruling]
        updated = store.update(job_id, rulings=rulings)
        assert updated is not None
        annotate_report(updated, store)
    return None


def judgement_items(job: Job, store: JobStore) -> list[dict[str, object]]:
    """A finished job's "needs your judgement" items, with their report index and any ruling."""
    from canonkeeper.report.display import headline

    if job.status != "done":
        return []
    report = load_report(store, job.id)
    if report is None:
        return []
    ruled = {r["finding"]: r for r in job.rulings}
    return [
        {
            "index": i,
            "headline": headline(f.candidate),
            "explanation": f.explanation,
            "ruling": ruled.get(str(i)),
        }
        for i, f in enumerate(report.findings)
        if f.verdict == "ambiguous"
    ]
