"""A real AI agent buying from Preflight over MCP, recorded and filmed.

    canonkeeper serve --provider mock --port 8791        # with PayPal sandbox keys in the env
    /usr/bin/python3 scripts/video/agent_session.py --base http://127.0.0.1:8791 --out build/agent

Claude Code (`claude -p`, headless) is connected to the server's MCP endpoint and allowed only
Preflight's tools. It is asked, in four turns, what an author would ask: open a budget; (after
the person approves it) audit the demo series from it; check the demo draft; close the budget.
The one step an agent can never take, approving the PayPal payment, is done here as the person
would: PayPal's card button, as a guest, with PayPal's published sandbox test card, on this
local server only. Every tool call and every reply is the agent's own, saved to
`transcript.json`; the film (`agent.webm`) replays that transcript in a plain chat panel and
ends on the budget's page. `scripts/video/clip.py` turns it into the GIF and MP4. A second
film, `agent-video.webm`, is the submission video's assistant beat (`record.py --agent-clip`):
the same transcript under a generic title, with shorter holds.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import subprocess
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

W, H = 1280, 720
TURNS = [
    "Open a $20 Preflight budget for checking my drafts, at most $6 a purchase.",
    "I've approved it on PayPal. Audit the Saltmarsh demo series and pay from the budget.",
    "The Oswin Reed one is wrong: he only faked his death in The Wreck. Mark it.",
    "Now check the demo series' next draft against it, from the budget, and tell me what you "
    "found.",
    "Thanks. Close the budget.",
]
MARK_TURN = 2  # the turn whose answer carries a confirm link for the person
SYSTEM = (
    "You are an author's assistant. Use only the Preflight tools. Keep replies short: two or "
    "three sentences, or a short list. Never claim to approve a payment: the person does that."
)


def claude(prompt: str, mcp_config: Path, session: str | None) -> tuple[str, list[dict]]:
    """One headless turn; returns (session id, the stream's events)."""
    args = [
        "claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--allowedTools", "mcp__preflight", "--append-system-prompt", SYSTEM,
        "--max-turns", "16",
    ]  # fmt: skip
    if session:
        args += ["--resume", session]
    run = subprocess.run(args, capture_output=True, text=True, timeout=900, check=False)
    events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
    sid = next((e.get("session_id") for e in events if e.get("session_id")), session or "")
    if run.returncode != 0 and not events:
        raise RuntimeError(run.stderr[-2000:])
    return sid, events


def steps(events: list[dict]) -> list[dict]:
    """The turn as the person would see it: Preflight tool calls with a one-line result, and
    replies. The client's own housekeeping (loading tool schemas) is left out; a spending key
    is shown masked."""
    out: list[dict] = []
    names: dict[str, str] = {}
    for e in events:
        content = (e.get("message") or {}).get("content") or []
        if e.get("type") == "assistant":
            for c in content:
                if c.get("type") == "text" and c["text"].strip():
                    out.append({"kind": "ai", "text": c["text"].strip()})
                elif c.get("type") == "tool_use":
                    names[c.get("id", "")] = c["name"]
                    if not c["name"].startswith("mcp__preflight__"):
                        continue
                    name = c["name"].removeprefix("mcp__preflight__")
                    args = {k: v for k, v in c.get("input", {}).items() if k != "text"}
                    if "budget_key" in args:
                        args["budget_key"] = "pfk_…"
                    out.append({"kind": "call", "name": name, "args": args})
        elif e.get("type") == "user":
            for c in content:
                if not (isinstance(c, dict) and c.get("type") == "tool_result"):
                    continue
                name = names.get(c.get("tool_use_id", ""), "")
                if not name.startswith("mcp__preflight__"):
                    continue
                body = c.get("content")
                text = (
                    body
                    if isinstance(body, str)
                    else " ".join(b.get("text", "") for b in body or [] if isinstance(b, dict))
                )
                out.append({"kind": "result", "text": summarize(text, name)})
    return out


def summarize(text: str, tool: str = "") -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text[:110]
    if isinstance(data, dict):
        data = data.get("result", data)
    if not isinstance(data, dict):
        return str(data)[:110]
    keep = ["status", "price", "paid", "budget_left", "contradictions", "amount", "reason"]
    if tool.endswith("open_budget"):
        keep.append("approve_url")
    if data.get("confirm_url"):
        return "a link for the person to confirm it: nothing marked, no money moved yet"
    parts = []
    for k in keep:
        if k in data and data[k] not in (None, ""):
            v = data[k]
            if isinstance(v, dict):
                v = v.get("amount") or v.get("total") or json.dumps(v)[:40]
            if k == "approve_url":
                v = "(a PayPal link for the person)"
            parts.append(f"{k}: {v}")
    refunds = data.get("refunds")
    if tool.endswith("close_budget") and isinstance(refunds, list) and refunds:
        back = sum(float(r.get("amount", 0)) for r in refunds if isinstance(r, dict))
        parts.append(f"refunded to PayPal: {back:.2f}")
    if "findings" in data and isinstance(data["findings"], list):
        parts.append(f"{len(data['findings'])} findings")
    return " · ".join(parts)[:120] or "ok"


def approve_url(events: list[dict]) -> str:
    blob = json.dumps(events)
    m = re.search(r'"approve_url\\?"?:\s*\\?"(https?://[^"\\]+)', blob)
    budget = re.search(r'"budget_url\\?"?:\s*\\?"(https?://[^"\\]+)', blob)
    if not budget:
        raise RuntimeError("the agent did not open a budget")
    return (m.group(1) if m else "") + "\n" + budget.group(1)


async def approve_budget(budget_url: str, shots: Path) -> None:
    """The person approves the budget's PayPal payment: PayPal's card button, as a guest."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("rec", Path(__file__).with_name("record.py"))
    rec = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(rec)  # type: ignore[union-attr]
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": W, "height": H})
        page = await ctx.new_page()
        await page.goto(budget_url)
        await page.wait_for_timeout(5000)
        await rec.pay_by_card(page)
        await page.wait_for_selector("h1 .status.active", timeout=90000)
        await page.screenshot(path=str(shots / "budget-active.png"))
        await browser.close()


async def confirm_mark(url: str) -> None:
    """The person confirms the finding the assistant asked about (the money moves on this)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": W, "height": H})
        await page.goto(url)
        await page.locator(".item.asked button[data-testid^=mark-wrong]").click()
        await page.wait_for_load_state()
        await browser.close()


CHAT = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#f4f1ea;font:19px/1.45 "DejaVu Sans",system-ui,sans-serif;color:#1d1b18}
.top{background:#1d1b18;color:#f4f1ea;padding:13px 28px;font-weight:600}
.top span{opacity:.65;font-weight:400;margin-left:10px}
#log{padding:20px 56px 120px;max-width:1120px;margin:0 auto}
.m{margin:10px 0;padding:11px 17px;border-radius:14px;max-width:880px;white-space:pre-wrap;overflow-wrap:anywhere}
.you{background:#3b4f8a;color:#fff;margin-left:auto;width:fit-content}
.ai{background:#fff;border:1px solid #e3ddd0}
.tool{font:14px/1.4 "DejaVu Sans Mono",monospace;color:#6b655c;margin:3px 0 3px 6px}
.tool b{color:#3b4f8a}
.you-act{font:15px/1.4 "DejaVu Sans",sans-serif;color:#2f6b5a;margin:8px 0 8px auto;width:fit-content}
</style><body><div class="top">__TITLE__<span>__SUB__</span></div><div id="log"></div></body>"""


def markdown(text: str) -> str:
    """The little Markdown a chat reply uses (bold, italics), as a chat window shows it."""
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)


ADD = """([cls, text]) => { const d = document.createElement('div'); d.className = cls;
d.innerHTML = text; document.getElementById('log').appendChild(d);
window.scrollTo(0, document.body.scrollHeight); }"""


# The /assistant page and README name the agent; the submission video may not show third-party
# trademarks, so its cut uses a generic title and shorter holds (it has about a minute).
PAGE_TITLE = "Claude, connected to Preflight over MCP"
VIDEO_TITLE = "Your AI assistant, connected to Preflight over MCP"


async def film(
    transcript: dict,
    out: Path,
    budget_url: str,
    *,
    title: str = PAGE_TITLE,
    pace: float = 1.0,
    name: str = "agent.webm",
) -> Path:
    """Replay the transcript in a chat panel and end on the budget's page. `pace` scales every
    hold (0.75 = a quarter shorter); the agent's words are never cut."""
    page_html = CHAT.replace("__TITLE__", html.escape(title)).replace(
        "__SUB__", "a recorded session · " + html.escape(transcript["mcp"]) + " · PayPal sandbox"
    )

    def hold(ms: float) -> int:
        return int(ms * pace)

    chat = out / "chat.html"
    chat.write_text(page_html, encoding="utf-8")
    raw = out / "raw"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": W, "height": H}, record_video_dir=str(raw),
            record_video_size={"width": W, "height": H},
        )  # fmt: skip
        page: Page = await ctx.new_page()
        t0 = time.monotonic()
        cues: list[dict] = []  # for narrate.py: when each turn and each tool call appears

        def cue(text: str) -> None:
            cues.append({"t": round(time.monotonic() - t0, 2), "text": text})

        await page.goto(chat.as_uri())
        await page.wait_for_timeout(hold(800))
        for n, turn in enumerate(transcript["turns"], 1):
            cue(f"turn:{n}")
            await page.evaluate(ADD, ["m you", html.escape(turn["prompt"])])
            await page.wait_for_timeout(hold(1300))
            for s in turn["steps"]:
                if s["kind"] == "call":
                    cue(f"call:{s['name']}")
                    args = ", ".join(
                        f"{k}={json.dumps(v, ensure_ascii=False)[:40]}"
                        for k, v in s["args"].items()
                    )
                    await page.evaluate(ADD, ["tool", f"→ <b>{s['name']}</b>({html.escape(args)})"])
                    await page.wait_for_timeout(hold(500))
                elif s["kind"] == "result":
                    await page.evaluate(ADD, ["tool", f"← {html.escape(s['text'])}"])
                    await page.wait_for_timeout(hold(700))
                else:
                    await page.evaluate(ADD, ["m ai", markdown(s["text"])])
                    await page.wait_for_timeout(hold(1000 * min(8.0, 1.8 + len(s["text"]) / 45)))
            if turn.get("then"):
                cue(f"then:{n}")
                await page.evaluate(ADD, ["you-act", html.escape(turn["then"])])
                await page.wait_for_timeout(hold(1600))
        cue("budget")
        await page.goto(budget_url)
        await page.wait_for_timeout(hold(3500))
        await page.evaluate("window.scrollTo({top: 400, behavior: 'smooth'})")
        await page.wait_for_timeout(hold(2500))
        await ctx.close()
        await browser.close()
    video = sorted(raw.glob("*.webm"), key=lambda f: f.stat().st_mtime)[-1]
    dest = out / name
    video.replace(dest)
    dest.with_suffix(".cues.json").write_text(json.dumps(cues, indent=1), encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8791")
    ap.add_argument("--out", type=Path, default=Path("build/agent"))
    ap.add_argument("--film-only", action="store_true", help="re-film a saved transcript")
    ap.add_argument(
        "--video-pace", type=float, default=0.8,
        help="also film agent-video.webm for the submission cut: a generic title, holds x this",
    )  # fmt: skip
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    saved = a.out / "transcript.json"
    if a.film_only:
        transcript = json.loads(saved.read_text(encoding="utf-8"))
        for i, turn in enumerate(transcript["turns"]):  # re-read the agent's own events
            raw = a.out / f"turn-{i + 1}.events.json"
            if raw.exists():
                turn["steps"] = steps(json.loads(raw.read_text(encoding="utf-8")))
        saved.write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    else:
        cfg = a.out / "mcp.json"
        mcp = f"{a.base.rstrip('/')}/mcp"
        cfg.write_text(json.dumps({"mcpServers": {"preflight": {"type": "http", "url": mcp}}}))
        transcript = {"mcp": mcp, "turns": [], "recorded_at": time.strftime("%Y-%m-%d %H:%M")}
        session = None
        budget_url = ""
        for i, prompt in enumerate(TURNS):
            session, events = claude(prompt, cfg, session)
            turn = {"prompt": prompt, "steps": steps(events)}
            raw = re.sub(r"pfk_[A-Za-z0-9_-]+", "pfk_…", json.dumps(events))
            (a.out / f"turn-{i + 1}.events.json").write_text(raw, encoding="utf-8")
            print(f"turn {i + 1}: {len(turn['steps'])} steps")
            if i == 0:
                _approve, budget_url = approve_url(events).split("\n")
                asyncio.run(approve_budget(budget_url, a.out))
                turn["then"] = "You approve the $20 on PayPal (sandbox, test money)."
            if i == MARK_TURN:
                link = re.search(r'confirm_url\\?"?:\s*\\?"(https?://[^"\\]+)', json.dumps(events))
                if link:
                    asyncio.run(confirm_mark(link.group(1).replace("\\u0026", "&")))
                    turn["then"] = "You confirm it on your job page: $0.50 back to the budget."
            transcript["turns"].append(turn)
        transcript["budget_url"] = budget_url
        saved.write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    print(asyncio.run(film(transcript, a.out, transcript["budget_url"])))
    if a.video_pace:
        video = film(
            transcript, a.out, transcript["budget_url"], title=VIDEO_TITLE, pace=a.video_pace,
            name="agent-video.webm",
        )  # fmt: skip
        print(asyncio.run(video))


if __name__ == "__main__":
    main()
