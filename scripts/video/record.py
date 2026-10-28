"""Record the demo video's scenes from the running app, with captions drawn into the page.

    canonkeeper serve --provider mock --port 8787          # demo mode (rule-based, fake PayPal)
    canonkeeper demo --dest build/demo                     # the demo series and its next draft
    python scripts/video/record.py --out build/video --draft build/demo/draft-04-the-ledger.txt
    python scripts/video/assemble.py build/video                # -> build/video/preflight.mp4

The final cut uses a real agent session for the assistant beat instead of the scripted calls:
run scripts/video/agent_session.py first, then `record.py --agent-clip <its agent-video.webm>`.

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
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.async_api import Locator, Page, async_playwright

W, H = 1280, 720
BASE = "http://127.0.0.1:8787"
# PayPal's published sandbox test card (developer.paypal.com/credit-card-number-generator):
# against a sandbox server the checkout scenes pay through PayPal's real card pop-up as a guest.
CARD = "4012000033330026"

# PayPal's card form opens in a pop-up, recorded as its own video; the recorder splices it into
# the scene at the moment it opened. (opened, closed, pop-up page), seconds from the scene start.
SPLITS: list[tuple[float, float, Page]] = []
CLIP_T0 = 0.0

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


PACE = 0.8  # caption hold times are scaled by this to keep the cut under three minutes


async def caption(page: Page, text: str, hold: float) -> None:
    await page.evaluate(CAPTION_JS, text)
    await page.wait_for_timeout(int(hold * PACE * 1000))


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


def sandbox(page: Page) -> bool:
    """The server takes payment through PayPal's own buttons (sandbox), not the fake page."""
    return any("smart/buttons" in f.url for f in page.frames)


async def guest_button(pop: Page, timeout_s: float = 45) -> Locator:
    """PayPal's guest "Pay Now" button. Since late Oct 2026 the card form opens with "Save info
    & create your PayPal account" switched on (its button reads "Create Account & Pay Now"):
    switch it off. Older forms have a separate guest button. Never returns a button whose text
    offers to create an account."""
    switch = pop.locator("input[role=switch], input[type=checkbox][id^=Switch]")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await switch.count() and await switch.first.is_checked():
            label = pop.locator(f"label[for='{await switch.first.get_attribute('id')}']")
            if await label.count():
                await label.first.click()
            else:
                await switch.first.click(force=True)
            await pop.wait_for_timeout(1500)
            continue
        for candidate in (
            pop.locator("[data-testid=pomaGuestSubmitButton]"),
            pop.get_by_role("button", name="Pay Now", exact=True),
        ):
            if await candidate.count() and await candidate.first.is_visible():
                text = (await candidate.first.inner_text()).lower()
                if "create" not in text and "account" not in text:
                    return candidate.first
        await pop.wait_for_timeout(1000)
    shot = Path(f"paypal-popup-{int(time.time())}.png")
    await pop.screenshot(path=str(shot), full_page=True)
    raise RuntimeError(f"no guest checkout button on PayPal's form; see {shot.resolve()}")


async def pay_by_card(page: Page) -> None:
    """Pay through PayPal's card button as a guest with the published sandbox test card."""
    frame = next(f for f in page.frames if "smart/buttons" in f.url)
    opened = time.monotonic() - CLIP_T0
    async with page.context.expect_page() as info:
        await frame.click("[data-funding-source='card']")
    pop = await info.value
    await pop.set_viewport_size({"width": W, "height": H})  # film it full width, not a sliver
    await pop.wait_for_load_state()
    await pop.wait_for_timeout(3500)

    async def no_cookies() -> None:
        close = pop.locator("#bannerCloseButton")
        if await close.is_visible():
            await close.click()  # close PayPal's cookie banner without accepting

    await no_cookies()
    await pop.evaluate(CAPTION_JS, "PayPal's own checkout. Sandbox: test money only.")
    await pop.select_option("#country", "US")
    await pop.wait_for_timeout(1500)
    for sel, val in [
        ("#cardNumber", CARD), ("#cardExpiry", "11/29"), ("#cardCvv", "321"),
        ("#firstName", "Test"), ("#lastName", "Buyer"), ("#billingLine1", "2211 North First Street"),
        ("#billingCity", "San Jose"), ("#billingPostalCode", "95131"),
        ("#phone", "4085550101"), ("#email", "test-buyer@example.com"),
    ]:  # fmt: skip
        await pop.fill(sel, val)
        await pop.wait_for_timeout(120)
        await no_cookies()  # PayPal's banner comes back over the caption
    await pop.press("#email", "Tab")  # PayPal checks the email before offering guest checkout
    await pop.select_option("#billingState", "CA")
    await no_cookies()
    button = await guest_button(pop)
    await pop.wait_for_timeout(500)
    try:
        await button.click(timeout=20000)  # pay as a guest: never the create-account button
        # PayPal may ask to confirm an address it cannot match: keep the one entered
        keep = pop.get_by_role("button", name="Continue", exact=True)
        for _ in range(20):
            if pop.is_closed():
                break
            if await keep.count() and await keep.first.is_visible():
                await keep.first.click()
                break
            await asyncio.sleep(1)
        if not pop.is_closed():
            await pop.wait_for_event("close", timeout=90000)
    except Exception:
        shot = Path(f"paypal-popup-{int(time.time())}.png")
        await pop.screenshot(path=str(shot), full_page=True)
        raise RuntimeError(f"PayPal's pop-up did not complete; see {shot.resolve()}") from None
    SPLITS.append((opened, time.monotonic() - CLIP_T0, pop))


def _cut(src: Path, dst: Path, start: float, end: float | None = None) -> None:
    args = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(src)]
    if end is not None:
        args += ["-t", f"{max(0.1, end - start):.2f}"]
    args += ["-c:v", "libvpx", "-b:v", "3M", "-deadline", "realtime", "-an", str(dst)]
    subprocess.run(args, check=True)


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
    # the triage grid: every finding on one screen, the author's own rulings beside the model's
    await smooth_scroll_to(page, "document.getElementById('triage')", offset=10)
    await caption(page, "Every finding in one grid: sort it, filter it, search it.", 3.5)
    await caption(page, "The author's own rulings sit beside the model's calls.", 3)
    await page.click("#triage-grid .ag-row[row-index='0'] [col-id='finding']")
    await page.wait_for_timeout(900)
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
    # every finding links to its own row in the AG Grid explorer: open Watson's
    await page.goto(
        f"{BASE}/sample/bible?view=bybook&disputed=1"
        "&focus=John%20Watson%7Cwound&book=The%20Sign%20of%20the%20Four"
    )
    await page.wait_for_selector(".ag-row")
    await page.wait_for_timeout(1200)
    await caption(page, "Each finding opens the series bible: one column per book.", 3.5)
    await click_bar(page, "Return")
    await caption(page, "Click a bar: the facts that book changes, beside what came before.", 4)


async def click_bar(page: Page, label: str) -> None:
    """Click one bar of the bible's AG Charts bar chart (drawn on a canvas, so by position: the
    bars share the width evenly, one per book after the first)."""
    books = await page.evaluate(
        "[...document.querySelectorAll('#grid .ag-header-cell[col-id^=\"book:\"]')]"
        ".map(e => e.getAttribute('col-id'))"
    )
    n = max(1, len(books) - 1)
    k = next((i for i, b in enumerate(books[1:]) if label in b), n - 1)
    box = await page.locator("#chart").bounding_box()
    assert box, "the bible page drew no chart"
    left, right = box["x"] + 30, box["x"] + box["width"] - 8
    x, y = left + (k + 0.5) * (right - left) / n, box["y"] + box["height"] * 0.72
    await page.mouse.move(x, y, steps=12)
    await page.wait_for_timeout(700)
    await page.mouse.click(x, y)
    await page.wait_for_function(
        "document.getElementById('chart-note').innerText.includes('Showing')"
    )


async def scene_buy(page: Page) -> str:
    await page.goto(BASE)
    await caption(page, "Preflight: an author uploads a series, or tries the demo series.", 3.5)
    await page.click("form[action='/quote-demo'] button")
    await page.wait_for_load_state("networkidle")
    await caption(
        page, "The price is known before anything runs: it is a function of the token count.", 4
    )
    await caption(page, "$5 base + $4 per 100k tokens, with the arithmetic on the page.", 3)
    if sandbox(page):
        await caption(page, "Pay with PayPal: PayPal, Venmo, Pay Later or a card.", 3)
        await pay_by_card(page)
        # the quote already lives at /jobs/<id>: wait for the job page itself, past "quoted"
        await page.wait_for_selector("h1 .status:not(.quoted)", timeout=60000)
        await page.wait_for_load_state("networkidle")
        await caption(page, "PayPal captured it. Only now does anything reach a model.", 3.5)
    else:
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
</style><body><div class="top">Your AI assistant<span>connected to Preflight over MCP · __BASE__/mcp</span></div>
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
    budget_url = b["budget_url"]
    await page.goto(budget_url)
    await page.wait_for_timeout(4000)
    if sandbox(page):
        await caption(page, "The person approves the budget with PayPal: only they can.", 3)
        await pay_by_card(page)
        await page.wait_for_timeout(5000)
        await page.goto(budget_url)
    else:
        await page.goto(b["approve_url"])
        await caption(page, "The person approves on PayPal (simulated here).", 3)
        await page.click("form button")
        await page.wait_for_load_state("networkidle")
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
    ap.add_argument("--draft", type=Path, help="the draft the scripted assistant checks")
    ap.add_argument("--bench", default="", help="one line of benchmark numbers for the close card")
    ap.add_argument("--base", default=BASE, help="the running server (demo mode)")
    ap.add_argument(
        "--agent-clip", type=Path,
        help="a real agent session's film (agent_session.py's agent-video.webm): used as the "
        "assistant beat, in place of the scripted MCP calls and the in-charge scene",
    )  # fmt: skip
    a = ap.parse_args()
    if not (a.agent_clip or a.draft):
        ap.error("give --agent-clip (a real agent session) or --draft (the scripted one)")
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
            global CLIP_T0
            CLIP_T0 = time.monotonic()
            SPLITS.clear()
            result = await fn(page, *args)
            video = page.video
            await ctx.close()
            src = Path(await video.path()) if video else None
            if not src:
                return result
            # the scene up to each PayPal pop-up, the pop-up itself, then the rest
            start = 0.0
            for k, (opened, closed, pop) in enumerate(list(SPLITS)):
                _cut(src, a.out / f"{len(order):02d}-{name}.webm", start, opened)
                order.append(name)
                popv = Path(await pop.video.path()) if pop.video else None
                if popv:
                    popv.rename(a.out / f"{len(order):02d}-{name}-paypal{k}.webm")
                    order.append(f"{name}-paypal{k}")
                name, start = f"{name}-after{k}", closed
            if SPLITS:
                _cut(src, a.out / f"{len(order):02d}-{name}.webm", start)
            else:
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
        if a.agent_clip:
            # the real session already holds what in-charge shows: a finding the person marks
            # wrong, money back to the budget, the budget closed and the rest refunded
            shutil.copyfile(a.agent_clip, a.out / f"{len(order):02d}-agent.webm")
            order.append("agent")
        else:
            budget_url = await record("agent", scene_agent, a.out, job_url, a.draft)
            await record("in-charge", scene_in_charge, job_url, budget_url)
        await record(
            "honest",
            scene_card,
            a.out,
            "honest",
            "<p class='big'>Measured, not eyeballed.</p>"
            f"<p>{html.escape(a.bench) or 'Contradictions are planted in the six Holmes novels and the whole pipeline is scored: precision 0.80, recall 0.40 on the last run, misses listed in the README.'}</p>"
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
    # PayPal's form fills are real but long: the cut plays them fast-forwarded
    fast = {n: 1.7 for n in order if "-paypal" in n}
    (a.out / "speeds.json").write_text(json.dumps(fast), encoding="utf-8")
    print("recorded:", order)


if __name__ == "__main__":
    asyncio.run(main())
