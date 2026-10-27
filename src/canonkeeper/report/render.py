from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from canonkeeper.models import AuditReport
from canonkeeper.report.display import check, fact, headline, where

_env = Environment(
    loader=PackageLoader("canonkeeper.report", "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters.update(headline=headline, where=where, fact=fact, check=check)


def write_report(
    report: AuditReport, out_dir: Path, *, back_url: str | None = None
) -> tuple[Path, Path]:
    """`back_url`: where the report's header links back to (its job page on Preflight)."""
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
        ),
        encoding="utf-8",
    )
    return html_path, json_path
