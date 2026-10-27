"""Regenerate docs/img/preflight-quote.png and preflight-job.png from a running demo server.

canonkeeper serve --provider mock --port 8787    # demo mode: fake PayPal, rule-based model
python scripts/preflight_images.py
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8787"


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 760})
        pg.goto(BASE)
        pg.locator("form[action='/quote-demo'] button").click()
        pg.wait_for_load_state()
        pg.screenshot(path=str(ROOT / "docs/img/preflight-quote.png"))
        pg.locator("form[action$='/order'] button").first.click()
        pg.wait_for_load_state()
        pg.click("button:has-text('Pay Now')")
        pg.wait_for_load_state()
        for _ in range(60):
            if "Open the report" in pg.content():
                break
            time.sleep(1)
            pg.reload()
        pg.screenshot(path=str(ROOT / "docs/img/preflight-job.png"))
        b.close()
    print("wrote docs/img/preflight-quote.png, docs/img/preflight-job.png")


if __name__ == "__main__":
    main()
