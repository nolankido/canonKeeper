"""Record the demo video's scenes from the running app, with captions drawn into the page.

    canonkeeper serve --provider mock --port 8787          # demo mode (rule-based, fake PayPal)
    canonkeeper demo --dest build/demo                     # the demo series and its next draft
    python scripts/video/record.py --out build/video --draft build/demo/draft-04-the-ledger.txt
    python scripts/video/assemble.py build/video                # -> build/video/preflight.mp4

The scenes follow docs/DEMO_SCRIPT.md (v2). The assistant scene drives the server's own MCP
endpoint (/mcp, JSON-RPC over HTTP) the way Claude Desktop would, and films the person's side:
the PayPal approval and the budget page.

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
import urllib.request
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
    await page.goto(f"{BASE}/sample/bible")
    await page.wait_for_timeout(1500)
    await caption(page, "Underneath: the series bible, every fact tied to its sentence.", 3)
    await page.check("#disputed")
    await page.wait_for_timeout(600)
    await page.click(".ag-row[row-index=\"0\"] >> nth=-1")
    await caption(page, "Filter to where the books disagree; open any passage.", 3.5)


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


_rpc_id = 0


def mcp_call(tool: str, **arguments: object) -> dict:
    """One MCP tool call over the server's streamable HTTP endpoint (stateless, JSON replies)."""
    global _rpc_id
    _rpc_id += 1
    body = {"jsonrpc": "2.0", "id": _rpc_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}  # fmt: skip
    req = urllib.request.Request(
        f"{BASE}/mcp",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.loads(r.read())
    result = reply["result"]
    if result.get("isError"):
        raise RuntimeError(result["content"][0]["text"])
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


CHAT_HTML = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#f4f1ea;font:20px/1.45 "DejaVu Sans",system-ui,sans-serif;color:#1d1b18}
.top{background:#1d1b18;color:#f4f1ea;padding:14px 28px;font-weight:600}
.top span{opacity:.6;font-weight:400;margin-left:10px}
#log{padding:22px 60px 140px;max-width:1100px;margin:0 auto}
.m{margin:12px 0;padding:12px 18px;border-radius:14px;max-width:860px;white-space:pre-wrap}
.you{background:#3b4f8a;color:#fff;margin-left:auto;width:fit-content}
.ai{background:#fff;border:1px solid #e3ddd0}
.tool{font:15px/1.4 "DejaVu Sans Mono",monospace;color:#6b655c;margin:4px 0 4px 6px}
.tool b{color:#3b4f8a}
</style><body><div class="top">Claude Desktop<span>connector: Preflight · __BASE__/mcp</span></div>
<div id="log"></div></body>"""

ADD_JS = """([cls, text]) => {
  const d = document.createElement('div'); d.className = cls; d.innerHTML = text;
  document.getElementById('log').appendChild(d); d.scrollIntoView({block: 'end'});
}"""


async def say(page: Page, cls: str, text: str, hold: float = 1.6) -> None:
    await page.evaluate(ADD_JS, [cls, text])
    await page.wait_for_timeout(int(hold * 1000))


async def tool(page: Page, name: str, args: str, result: str, hold: float = 1.4) -> None:
    await say(page, "tool", f"→ <b>{name}</b>({html.escape(args)})", 0.7)
    await say(page, "tool", f"← {html.escape(result)}", hold)


async def scene_agent(page: Page, out: Path, job_url: str, draft: Path) -> str:
    """Claude Desktop with the hosted connector: a budget the person funds once, then a draft
    check it buys on its own. Returns the budget page's URL."""
    chat = out / "chat.html"
    chat.write_text(CHAT_HTML.replace("__BASE__", BASE), encoding="utf-8")
    await page.goto(chat.as_uri())
    await caption(page, "The same shop serves AI assistants over MCP.", 2.5)
    await caption(page, "", 0.1)
    await say(page, "m you", "Open a $20 Preflight budget for checking my drafts.", 1.2)
    b = mcp_call("open_budget", amount_usd="20")
    await tool(
        page, "open_budget", 'amount_usd="20"', f"budget {b['budget_id']} · waiting for approval"
    )
    await say(page, "m ai", "I've opened it. Only you can approve the payment: "
              "<u>Approve $20.00 on PayPal</u>. I never see your PayPal login.", 2.2)  # fmt: skip
    await caption(page, "The assistant can open the order. Only the person can approve it.", 3)

    # the person, on PayPal
    await page.goto(b["approve_url"])
    await caption(
        page, "The person approves on PayPal (simulated here; the sandbox in the live demo).", 3
    )
    await page.click("form button")
    await page.wait_for_load_state("networkidle")
    budget_url = page.url.split("/return")[0]
    await page.goto(budget_url)
    await caption(
        page, "Funded once. From here the assistant works on its own, inside the limit.", 3.5
    )

    await page.goto(chat.as_uri())
    await say(page, "m you", "Open a $20 Preflight budget for checking my drafts.", 0.1)
    await say(page, "m ai", "Budget approved and active: $20.00.", 0.4)
    await say(page, "m you", f"Check my new draft, {draft.name}, against the series.", 1.2)
    job_id = job_url.rstrip("/").split("/")[-1]
    q = mcp_call(
        "check_draft", job_id=job_id, name=draft.name, text=draft.read_text(encoding="utf-8")
    )
    await tool(page, "check_draft", f'job_id="{job_id}", name="{draft.name}"',
               f"price ${q['price']['amount']} · {q['price']['explanation']}"[:110])  # fmt: skip
    paid = mcp_call("pay_with_budget", job_id=q["job_id"], budget_key=b["budget_key"])
    await tool(page, "pay_with_budget", f'job_id="{q["job_id"]}"',
               f"paid from the budget · ${paid['budget_left']} left")  # fmt: skip
    for _ in range(60):
        st = mcp_call("audit_status", job_id=q["job_id"])
        if st["status"] in ("done", "failed"):
            break
        await page.wait_for_timeout(500)
    await tool(page, "audit_status", f'job_id="{q["job_id"]}"', st["status"], 0.6)
    found = mcp_call("get_findings", job_id=q["job_id"])["findings"]
    await tool(page, "get_findings", f'job_id="{q["job_id"]}"', f"{len(found)} contradictions")
    lines = "".join(f"\n• {html.escape(f['summary'])}" for f in found[:3])
    await say(page, "m ai", f"Your draft disagrees with the published books in {len(found)} "
              f"places:{lines}\nEach has the published passage and a fix to the draft.", 3.5)  # fmt: skip
    await caption(page, "It never spends past the balance, and every spend is on a receipt.", 3.5)
    await page.goto(budget_url)
    await page.wait_for_timeout(2500)
    return budget_url


async def scene_in_charge(page: Page, job_url: str, budget_url: str) -> None:
    """The author stays in charge: mark a finding wrong (money back through PayPal), then close
    the budget (the unused rest refunded)."""
    await page.goto(job_url)
    await smooth_scroll_to(page, "document.getElementById('guarantee')", offset=20)
    await caption(page, "If a finding is wrong, the author says so...", 2.5)
    await page.click("details.mark summary")
    await page.fill("details.mark[open] input[name=note]", "a nickname, not a new name")
    await page.click("details.mark[open] button")
    await page.wait_for_load_state("networkidle")
    await smooth_scroll_to(page, "document.getElementById('guarantee')", offset=20)
    await caption(page, "...and $0.50 comes back through PayPal, up to half the price.", 3.5)
    await page.goto(budget_url)
    await page.check("form[data-testid=budget-close] input[name=confirm]")
    await caption(page, "Closing the budget refunds the rest to PayPal.", 2.5)
    await page.click("form[data-testid=budget-close] button")
    await page.wait_for_load_state("networkidle")
    await caption(page, "The unused balance is back in the person's PayPal account.", 3)
    await smooth_scroll_to(page, "document.querySelector('[data-testid=budget-ledger]')", offset=20)
    await page.wait_for_timeout(2500)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("build/video"))
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--bench", default="", help="one line of benchmark numbers for the close card")
    ap.add_argument("--base", default=BASE, help="the running server (demo mode)")
    a = ap.parse_args()
    globals()["BASE"] = a.base.rstrip("/")
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
            "problem",
            scene_card,
            a.out,
            "problem",
            "<p class='big'>Book six has to agree with book one.</p>"
            "<p>Preflight reads a whole series and shows every contradiction: both passages, "
            "and a fix for the newer book.</p><p>For series authors and editors. "
            "Paid per audit with PayPal.</p>",
            6,
        )
        await record("holmes", scene_holmes)
        job_url = await record("buy", scene_buy)
        budget_url = await record("agent", scene_agent, a.out, job_url, a.draft)
        await record("in-charge", scene_in_charge, job_url, budget_url)
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
