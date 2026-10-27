"""Record the demo video's scenes from the running app, with captions drawn into the page.

    canonkeeper serve --provider mock --port 8787          # demo mode (rule-based, fake PayPal)
    python scripts/mcp_flow_demo.py --auto-approve demo/books/*.txt > mcp.txt   # transcript
    python scripts/video/record.py --out build/video --mcp mcp.txt --books demo/books \\
        --draft demo/draft-04-the-ledger.txt
    python scripts/video/assemble.py build/video                # -> build/video/preflight.mp4

Needs Playwright (`pip install playwright`); the server must be in demo mode so the checkout
can be clicked through without a PayPal login. The Holmes scene uses /sample, the packaged
report from a real Gemini run. Each scene is recorded to its own .webm; assemble.py trims,
encodes and concatenates them.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
from pathlib import Path

from playwright.async_api import Page, async_playwright

W, H = 1280, 720
BASE = "http://127.0.0.1:8787"

CAPTION_JS = """
(text) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div');
    el.id = '__cap';
    el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
      'max-width:1100px;background:rgba(20,20,24,.88);color:#fff;font:600 24px/1.35 ' +
      '"DejaVu Sans",system-ui,sans-serif;padding:14px 22px;border-radius:12px;' +
      'z-index:99999;text-align:center;transition:opacity .25s;box-shadow:0 6px 24px rgba(0,0,0,.25)';
    document.body.appendChild(el);
  }
  el.style.opacity = text ? '1' : '0';
  if (text) el.textContent = text;
}
"""

CARD_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#f7f5f0;font-family:"DejaVu Serif",Georgia,serif;color:#1d1b18}}
.w{{max-width:1000px;padding:40px}} h1{{font-size:64px;margin:0 0 18px;letter-spacing:-1px}}
p{{font-size:30px;line-height:1.45;margin:10px 0;color:#3d3a35}} .k{{font-family:"DejaVu Sans Mono",monospace;
font-size:22px;color:#6b665e;margin-top:28px}} .big{{font-size:44px;font-weight:700;color:#1d1b18}}
</style></head><body><div class="w">{body}</div></body></html>"""


async def caption(page: Page, text: str, hold: float) -> None:
    await page.evaluate(CAPTION_JS, text)
    await page.wait_for_timeout(int(hold * 1000))


async def smooth_scroll_to(page: Page, selector_js: str, offset: int = 90, ms: int = 1200) -> None:
    """Scroll the element found by `selector_js` (a JS expression) to near the top. Uses the
    browser's scrollIntoView, which stays correct when the page is zoomed."""
    await page.evaluate(
        """([expr, offset]) => {
            const el = eval(expr); if (!el) return;
            el.style.scrollMarginTop = offset + 'px';
            el.scrollIntoView({behavior: 'smooth', block: 'start'});
        }""",
        [selector_js, offset],
    )
    await page.wait_for_timeout(ms + 200)


def card(body: str) -> str:
    return CARD_HTML.format(body=body)


async def scene_card(page: Page, out: Path, name: str, body: str, secs: float) -> None:
    f = out / f"{name}.html"
    f.write_text(card(body), encoding="utf-8")
    await page.goto(f.as_uri())
    await page.wait_for_timeout(int(secs * 1000))


async def scene_holmes(page: Page) -> None:
    await page.goto(f"{BASE}/sample")
    await page.evaluate("document.body.style.zoom = '0.72'")  # a whole finding fits on screen
    await caption(page, "Six Sherlock Holmes novels, read by the Gemini free tier.", 3.5)
    await caption(
        page, "3,963 facts in the series bible, each tied to the exact passage that states it.", 3.5
    )
    await smooth_scroll_to(page, "document.querySelector('div.f')", offset=20)
    await caption(page, "Watson's war wound: the shoulder in book 1, the leg in book 2.", 4)
    await caption(
        page, "The fix is aimed at the later book. The earlier one is already in print.", 3.5
    )
    await smooth_scroll_to(
        page,
        "[...document.querySelectorAll('div.f')].find(d => d.innerText.includes('landlady of Sherlock'))",
        offset=20,
    )
    await caption(page, "And the landlady: Mrs. Hudson in one story, Mrs. Turner in another.", 4.5)
    await page.evaluate(
        "document.querySelector('details') && (document.querySelector('details').open = true)"
    )
    await smooth_scroll_to(
        page,
        "[...document.querySelectorAll('div.f')].find(d => d.innerText.includes('Final Problem'))",
        offset=20,
    )
    await caption(
        page,
        "Holmes 'dies' at the Reichenbach Falls, then walks through The Hound...",
        3.5,
    )
    await caption(
        page,
        "...cleared, quoting why: The Hound is told from Watson's diary of earlier events.",
        4.5,
    )
    await caption(page, "Every cleared conflict must quote the passage that clears it.", 3.5)


async def scene_buy(page: Page) -> str:
    await page.goto(BASE)
    await caption(page, "Preflight: an author uploads a series, or tries the demo series.", 3.5)
    await page.click("form[action='/quote-demo'] button")
    await page.wait_for_load_state("networkidle")
    await caption(
        page, "The price is known before anything runs: it is a function of the token count.", 4
    )
    await caption(page, "$5 base + $4 per 100k tokens, with the arithmetic on the page.", 3)
    await page.click("form[action$='/order'] button")
    await page.wait_for_load_state("networkidle")
    await caption(
        page,
        "PayPal Orders v2: create the order, the buyer approves, we capture. "
        "(Simulated here; sandbox in the live demo.)",
        4.5,
    )
    await page.click("form button")
    await page.wait_for_load_state("networkidle")
    job_url = page.url
    await caption(page, "No model is called until the payment is captured.", 3)
    await page.wait_for_timeout(1500)
    await page.reload()
    await caption(page, "The audit ran: facts extracted, conflicts screened, each one judged.", 3)
    await page.click("a.btn[href$='/report']")
    await page.wait_for_load_state("networkidle")
    await page.evaluate("document.body.style.zoom = '0.8'")
    await caption(page, "Five planted errors, each with both passages and a fix.", 4)
    await page.mouse.wheel(0, 500)
    await page.wait_for_timeout(1500)
    return job_url


async def scene_check(page: Page, job_url: str, draft: Path) -> None:
    await page.goto(job_url)
    await smooth_scroll_to(page, "document.querySelector(\"form[action$='/check']\")", offset=60)
    await caption(
        page, "Writing the next book? Check the draft against the bible you already have.", 4
    )
    await page.set_input_files("#draft", str(draft))
    await page.click("form[action$='/check'] button")
    await page.wait_for_load_state("networkidle")
    await caption(page, "Priced on the draft alone: $2 base + $4 per 100k draft tokens.", 3.5)
    await page.click("form[action$='/order'] button")
    await page.wait_for_load_state("networkidle")
    await page.click("form button")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)
    await page.reload()
    await page.click("a.btn[href$='/report']")
    await page.wait_for_load_state("networkidle")
    await page.evaluate("document.body.style.zoom = '0.8'")
    await caption(
        page, "Two slips in the draft, each with the published passage it contradicts.", 4
    )
    await page.evaluate(
        "document.querySelector('details') && (document.querySelector('details').open = true)"
    )
    await smooth_scroll_to(
        page,
        "[...document.querySelectorAll('div.f')].find(d => d.innerText.includes('deliberate'))",
        offset=40,
    )
    await caption(
        page,
        "And the change the draft explains on the page — tinted glasses — is marked deliberate.",
        4.5,
    )


async def scene_mcp(page: Page, out: Path, transcript: str) -> None:
    lines = [ln for ln in transcript.splitlines() if ln.strip()][:60]
    body = "".join(f"<div class='l'>{html.escape(ln)}</div>" for ln in lines)
    doc = (
        "<!doctype html><meta charset='utf-8'><style>body{margin:0;background:#16161a;color:#e8e6e1;"
        "font:18px/1.5 'DejaVu Sans Mono',monospace;padding:36px 44px}"
        ".l{white-space:pre-wrap;word-break:break-all;opacity:0;margin:3px 0}.l.on{opacity:1}"
        ".l:has(+.l){}</style><body>"
        "<div style='color:#8ab4f8;margin-bottom:12px'>$ python scripts/mcp_flow_demo.py books/*.txt"
        "</div>" + body + "</body>"
    )
    f = out / "mcp.html"
    f.write_text(doc, encoding="utf-8")
    await page.goto(f.as_uri())
    await page.evaluate(CAPTION_JS, "An assistant can buy the same audit over MCP.")
    await page.evaluate(
        """async () => {
            const ls = [...document.querySelectorAll('.l')];
            for (const l of ls) { l.classList.add('on'); l.scrollIntoView({block: 'end'});
              await new Promise(r => setTimeout(r, 450)); }
        }"""
    )
    await caption(page, "It prices the job and creates the order...", 2.5)
    await caption(
        page,
        "...but confirm_payment refuses to run anything until the person approves on PayPal.",
        4.5,
    )
    await caption(page, "Agent-initiated, human-approved: the approval link goes to the person.", 4)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("build/video"))
    ap.add_argument(
        "--mcp", type=Path, required=True, help="transcript of scripts/mcp_flow_demo.py"
    )
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--bench", default="", help="one line of benchmark numbers for the close card")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    order: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def record(name: str, fn, *args):  # noqa: ANN001, ANN202
            ctx = await browser.new_context(
                viewport={"width": W, "height": H},
                record_video_dir=str(a.out / "raw"),
                record_video_size={"width": W, "height": H},
            )
            page = await ctx.new_page()
            result = await fn(page, *args)
            video = page.video
            await ctx.close()
            src = Path(await video.path()) if video else None
            if src:
                src.rename(a.out / f"{len(order):02d}-{name}.webm")
                order.append(name)
            return result

        await record(
            "title",
            scene_card,
            a.out,
            "title",
            "<h1>Preflight</h1><p>Pay-per-audit continuity checking for series authors.</p>"
            "<p class='k'>canonkeeper · PayPal AI Hackathon 2026</p>",
            4,
        )
        await record(
            "problem",
            scene_card,
            a.out,
            "problem",
            "<p class='big'>Book six has to agree with book one,<br>written three years earlier.</p>"
            "<p>Readers notice every slip. Today the only tool is a spreadsheet and memory.</p>",
            6,
        )
        await record("holmes", scene_holmes)
        job_url = await record("buy", scene_buy)
        await record("check", scene_check, job_url, a.draft)
        await record("mcp", scene_mcp, a.out, a.mcp.read_text(encoding="utf-8"))
        await record(
            "honest",
            scene_card,
            a.out,
            "honest",
            "<p class='big'>Measured, not eyeballed.</p>"
            f"<p>{html.escape(a.bench) or 'A seeded-defect benchmark plants contradictions in public-domain novels and scores the whole pipeline.'}</p>"
            "<p>Private manuscripts run on a local model: nothing leaves the machine.</p>",
            7,
        )
        await record(
            "close",
            scene_card,
            a.out,
            "close",
            "<h1>Preflight</h1><p>Pay for the audit. Get the proof.</p>"
            "<p class='k'>github.com/nolankido/canonKeeper · MIT · runs on a free tier, no card</p>",
            5,
        )
        await browser.close()
    (a.out / "order.json").write_text(json.dumps(order), encoding="utf-8")
    print("recorded:", order)


if __name__ == "__main__":
    asyncio.run(main())
