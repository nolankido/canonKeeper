from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from canonkeeper.models import AuditReport, Finding
from canonkeeper.report.display import _book, check, fact, headline, label, where

_env = Environment(
    loader=PackageLoader("canonkeeper.report", "templates"),
    autoescape=select_autoescape(["html"]),
)


def bible_focus(f: Finding) -> str:
    """The bible explorer's address for one finding: each book's value for that fact, with the
    later book's passage open."""
    claims = f.candidate.claims
    if not claims:
        return ""
    first = claims[0]
    # the first book that says something else: the "later passage" of the headline
    later = next((c for c in claims if _book(c) != _book(first)), claims[-1])
    q = {
        "view": "bybook",
        "focus": f"{first.subject}|{label(first.predicate)}",
        "book": _book(later),
    }
    return "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


_env.filters.update(headline=headline, where=where, fact=fact, check=check, bible_focus=bible_focus)


def model_credit(report: AuditReport) -> str:
    """One plain sentence on which models did the work, for a customer's report."""
    m = report.models
    if m.get("provider") == "mock":
        return "Findings from a rule-based stand-in (demo), not a model."
    judges = m.get("adjudicated_by") or m.get("adjudicate") or ""
    names = [re.sub(r"\s*[×x]\s*\d+$", "", j).strip() for j in str(judges).split(",")]
    names = [n for n in names if n]
    judged = " and ".join(names) if names else "a judging model"
    return f"Facts read by {m.get('extract', 'an extraction model')}; conflicts judged by {judged}."


def report_html(
    report: AuditReport,
    *,
    back_url: str | None = None,
    back_label: str = "← Back to the job page: rule on items, check a new draft",
    customer: bool = False,
    bible_url: str | None = None,
    shared: bool = False,
) -> str:
    """The report page as a string (`write_report` saves it; Preflight's read-only share link
    renders it on request, `shared=True`)."""
    return _env.get_template("report.html").render(
        r=report,
        errors=report.errors,
        others=[f for f in report.findings if not f.is_error],
        back_url=back_url,
        back_label=back_label,
        is_check=bool(report.books) and report.books[-1].endswith("(draft)"),
        customer=customer,
        bible_url=bible_url,
        shared=shared,
        credit=model_credit(report) if customer else "",
    )


def write_report(
    report: AuditReport,
    out_dir: Path,
    *,
    back_url: str | None = None,
    back_label: str = "← Back to the job page: rule on items, check a new draft",
    customer: bool = False,
    bible_url: str | None = None,
) -> tuple[Path, Path]:
    """`back_url`: where the report's header links back to (its job page on Preflight).
    `customer`: the report a paying author gets (Preflight and its sample): plain model credits
    and no list-price cost tile; the CLI's report keeps both for the person running it.
    `bible_url`: the series bible explorer; by default the `bible-grid.html` written beside the
    report (or one folder up, for a draft check), linked relatively."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if bible_url is None:
        for rel in ("bible-grid.html", "../bible-grid.html"):
            if (out_dir / rel).exists():
                bible_url = rel
                break
    json_path = out_dir / "report.json"
    html_path = out_dir / "report.html"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    html_path.write_text(
        report_html(
            report, back_url=back_url, back_label=back_label, customer=customer, bible_url=bible_url
        ),
        encoding="utf-8",
    )
    return html_path, json_path
