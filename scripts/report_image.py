"""Regenerate docs/img/report.png: the sample report's header, Watson card and landlady card.

python scripts/report_image.py   # needs Playwright's Chromium (preinstalled in CI images)
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/canonkeeper/preflight/samples/sherlock-holmes-report.html"
OUT = ROOT / "docs/img/report.png"
KEEP = ["Watson’s wound", "landlady of Sherlock"]


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 900})
        pg.goto(SRC.as_uri())
        bottom = pg.evaluate(
            """(keep) => {
              const cards = [...document.querySelectorAll('.f')];
              const idx = keep.map(k => cards.findIndex(c => c.textContent.includes(k)));
              let last = null;
              cards.forEach((c, i) => { if (!idx.includes(i)) c.style.display = 'none'; else last = c; });
              for (let n = last.nextElementSibling; n; n = n.nextElementSibling) n.style.display = 'none';
              return last.getBoundingClientRect().bottom + window.scrollY;
            }""",
            KEEP,
        )
        pg.screenshot(
            path=str(OUT),
            full_page=True,
            clip={"x": 0, "y": 0, "width": 1200, "height": bottom + 14},
        )
        b.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
