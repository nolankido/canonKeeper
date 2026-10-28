"""Run axe-core (the standard automated accessibility checker) on every Preflight page.

    npm install axe-core@4.10.2            # anywhere; pass its folder as the first argument
    python scripts/a11y_check.py <folder with node_modules> <ids.json> http://127.0.0.1:8787

`ids.json` names a finished job ("done"), a quoted one ("quoted") and a budget ("budget") on the
running server. Prints each rule broken per page; 0 everywhere on 2026-10-27. Exit code 1 when
anything is broken.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

AXE_RUN = (
    "async () => (await axe.run(document, {resultTypes: ['violations']})).violations.map(v => ("
    "{id: v.id, impact: v.impact, help: v.help, n: v.nodes.length,"
    " sample: v.nodes.slice(0, 2).map(n => n.target.join(' '))}))"
)


def main(axe_dir: str, ids_path: str, base: str) -> int:
    ids = json.loads(Path(ids_path).read_text(encoding="utf-8"))
    done, quoted, budget = ids["done"], ids["quoted"], ids["budget"]
    pages = {
        "home": "/", "job": f"/jobs/{done}", "quote": f"/jobs/{quoted}",
        "report": f"/jobs/{done}/report", "bible": f"/jobs/{done}/bible",
        "explorer": f"/jobs/{done}/bible-grid.html", "budget": f"/budget/{budget}",
        "sample": "/sample", "sample explorer": "/sample/bible", "error": "/jobs/nope",
        "assistant": "/assistant",
    }  # fmt: skip
    axe = (Path(axe_dir) / "node_modules/axe-core/axe.min.js").read_text(encoding="utf-8")
    broken = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, path in pages.items():
            page = browser.new_page(
                viewport={"width": 1280, "height": 900}, extra_http_headers={"accept": "text/html"}
            )
            page.goto(base + path)
            page.wait_for_timeout(1500)
            page.add_script_tag(content=axe)
            rules = page.evaluate(AXE_RUN)
            print(f"== {name}: {len(rules)} rules broken")
            for r in rules:
                print(f"  [{r['impact']}] {r['id']} x{r['n']}: {r['help']}")
                for target in r["sample"]:
                    print("     ", target[:160])
            broken += len(rules)
            page.close()
        browser.close()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
