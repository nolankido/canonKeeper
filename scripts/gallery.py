"""Build the Devpost gallery images (3:2, 1500x1000 at 1.5x) from the running site.

    /usr/bin/python3 scripts/gallery.py https://preflight-kq1o.onrender.com docs/gallery \
        [http://127.0.0.1:8791 <paid job id> <budget id>]

Each image is one claim with the screen that backs it: the report's first finding, the quote
with PayPal's buttons, the budget an assistant spends from, the AG Grid bible, the receipt with
a finding marked wrong. The quote it makes is deleted again. The budget and receipt screens come from
a local server holding a job and a budget paid in the PayPal sandbox (the optional three
arguments), or else from the sandbox screenshots in docs/img.
Needs Playwright (the system python3 in the dev container has it).
"""

from __future__ import annotations

import base64
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
W, H, SCALE = 1500, 1000, 1.5

SLIDE = """<!doctype html><meta charset="utf-8"><style>
:root {{ --bg:#faf8f3; --fg:#1d1a16; --muted:#6b655c; --line:#e6e1d7; --accent:#3b4f8a; }}
* {{ box-sizing: border-box; margin: 0; }}
body {{ width:{w}px; height:{h}px; background:var(--bg); color:var(--fg); overflow:hidden;
  font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif; display:grid;
  grid-template-columns: 470px 1fr; gap: 50px; padding: 70px 70px 60px; }}
.text {{ display:flex; flex-direction:column; justify-content:center; }}
.n {{ font: 600 18px ui-monospace,Menlo,monospace; color:var(--accent); letter-spacing:.08em; }}
h1 {{ font-size: 50px; line-height: 1.08; margin: 18px 0 24px; }}
p {{ font-size: 23px; line-height: 1.45; color: var(--muted); }}
.brand {{ margin-top: auto; font-size: 20px; color: var(--muted); }}
.brand b {{ color: var(--fg); }}
.shot {{ display:flex; align-items:center; justify-content:center; min-height:0; }}
.shot img {{ max-width:100%; max-height:{ih}px; border:1px solid var(--line); border-radius:14px;
  box-shadow: 0 18px 50px rgba(40,30,10,.13); background:#fff; }}
</style>
<div class="text"><div class="n">{n}</div><h1>{title}</h1><p>{body}</p>
<div class="brand"><b>Preflight</b> · pay-per-audit continuity checking</div></div>
<div class="shot"><img src="data:image/png;base64,{img}"></div>"""

SLIDES = [
    (
        "report",
        "Book six has to agree with book one",
        "Preflight reads a whole series and shows every contradiction: both passages, quoted, and "
        "a fix aimed at the newer book. Here, Watson's war wound moves from shoulder to leg.",
    ),
    (
        "quote",
        "The price before anything runs",
        "$5 plus $4 per 100,000 tokens, with the arithmetic on the page. Pay with PayPal, Venmo, "
        "Pay Later or a card. Nothing reaches a model until PayPal confirms the capture.",
    ),
    (
        "budget",
        "Your AI assistant pays, inside a limit you set",
        "Fund a budget once through PayPal. Here a real Claude session spent it over MCP: never "
        "past the balance or the $6 limit, every spend on this page; closing it refunded the rest.",
    ),
    (
        "bible",
        "Every fact, with the sentence it came from",
        "The series bible in AG Grid: 3,963 facts from six Sherlock Holmes books, one column per "
        "book, values that change from book to book marked. Click one to read its sentence.",
    ),
    (
        "job",
        "The author stays in charge",
        "Mark a finding wrong and $0.50 comes back through PayPal, up to half the price. Rule on "
        "what the model left to you. The report shows your call beside the model's.",
    ),
]


def shot(page: Page, selector: str, path: Path) -> Path:
    page.locator(selector).first.screenshot(path=str(path))
    return path


def union(page: Page, selectors: list[str], path: Path, pad: int = 0) -> Path:
    """One picture spanning several elements (top of the first to the bottom of the last)."""
    boxes = [page.locator(s).first.bounding_box() for s in selectors]
    top = min(b["y"] for b in boxes if b) - pad
    bottom = max(b["y"] + b["height"] for b in boxes if b) + pad
    left = min(b["x"] for b in boxes if b) - pad
    right = max(b["x"] + b["width"] for b in boxes if b) + pad
    clip = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    page.screenshot(path=str(path), clip=clip, full_page=True)
    return path


def capture_receipts(base: str, job: str, budget: str, tmp: Path) -> dict[str, Path]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1000}, device_scale_factor=2)
        page.goto(f"{base}/budget/{budget}")
        budget_shot = union(page, ["h1", "[data-testid=budget-ledger]"], tmp / "budget.png", pad=10)
        page.goto(f"{base}/jobs/{job}")
        job_shot = union(
            page, ["[data-testid=receipt]", "[data-testid=guarantee]"], tmp / "job.png", pad=2
        )
        browser.close()
    return {"budget": budget_shot, "job": job_shot}


def capture(base: str, tmp: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1000}, device_scale_factor=2)
        page.goto(f"{base}/sample")
        out["report"] = shot(page, ".f", tmp / "report.png")

        page.goto(f"{base}/")
        page.click("[data-testid=cta-demo]")
        page.wait_for_selector("iframe[title*=PayPal]", timeout=30000)
        page.wait_for_timeout(2500)
        out["quote"] = shot(page, ".card", tmp / "quote.png")
        job = re.search(r"/jobs/([A-Za-z0-9_-]{11})/", page.content())

        page.set_viewport_size({"width": 1240, "height": 900})
        page.goto(f"{base}/sample/bible")
        page.wait_for_selector(".ag-row", timeout=30000)
        page.get_by_text("Each fact by book").first.click()
        page.get_by_label("Only where the books disagree").check()
        page.wait_for_timeout(800)
        page.locator(".ag-cell", has_text="leg").filter(has_text="leg").first.click()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(tmp / "bible.png"))
        out["bible"] = tmp / "bible.png"
        browser.close()
    if job:  # leave nothing behind on the site
        data = urllib.parse.urlencode({"confirm": "yes"}).encode()
        urllib.request.urlopen(f"{base}/jobs/{job.group(1)}/delete", data=data, timeout=30)
    out["budget"] = ROOT / "docs/img/preflight-budget.png"
    out["job"] = ROOT / "docs/img/preflight-job.png"
    return out


def main(base: str, dest: str, receipts: str = "", job: str = "", budget: str = "") -> None:
    out_dir = Path(dest)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".shots"
    tmp.mkdir(exist_ok=True)
    shots = capture(base.rstrip("/"), tmp)
    if receipts:
        shots.update(capture_receipts(receipts.rstrip("/"), job, budget, tmp))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        for i, (key, title, body) in enumerate(SLIDES, 1):
            img = base64.b64encode(shots[key].read_bytes()).decode()
            html = SLIDE.format(
                w=W, h=H, ih=H - 130, n=f"{i:02d} / {len(SLIDES):02d}", title=title, body=body,
                img=img,
            )  # fmt: skip
            page.set_content(html)
            page.wait_for_timeout(200)
            path = out_dir / f"{i:02d}-{key}.png"
            page.screenshot(path=str(path))
            print(path)
        browser.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


if __name__ == "__main__":
    main(*sys.argv[1:6])
