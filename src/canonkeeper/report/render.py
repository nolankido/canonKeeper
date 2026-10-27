from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from canonkeeper.models import AuditReport
from canonkeeper.report.display import check, fact, headline, where

_env = Environment(
    loader=PackageLoader("canonkeeper.report", "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters.update(headline=headline, where=where, fact=fact, check=check)


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


def write_report(
    report: AuditReport,
    out_dir: Path,
    *,
    back_url: str | None = None,
    back_label: str = "← Back to the job page: rule on items, check a new draft",
    customer: bool = False,
) -> tuple[Path, Path]:
    """`back_url`: where the report's header links back to (its job page on Preflight).
    `customer`: the report a paying author gets (Preflight and its sample): plain model credits
    and no list-price cost tile; the CLI's report keeps both for the person running it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    html_path = out_dir / "report.html"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    tmpl = _env.get_template("report.html")
    html_path.write_text(
        tmpl.render(
            r=report,
            errors=report.errors,
            others=[f for f in report.findings if not f.is_error],
            back_url=back_url,
            back_label=back_label,
            customer=customer,
            credit=model_credit(report) if customer else "",
        ),
        encoding="utf-8",
    )
    return html_path, json_path
