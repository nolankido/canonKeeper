# Demo script — Preflight (PayPal AI Hackathon 2026)

Target 2:50, hard limit 3:00, public on YouTube. Record by **Nov 9**; submit by **Nov 12, 12:00 PT**.

Judging criteria (unweighted): Technological Implementation · Design · Potential Impact ·
Innovation · Presentation. Special award we are aiming at: **Best Use of Agentic Commerce** (with the AG Grid sponsor
prize for the bible explorer). Every beat below names what it is for.

The video must show the project working end-to-end, on the machine it was built for, with no
third-party music or copyrighted material. Everything shown is public-domain text or our own
invented series. Warm every cache first: nothing on screen should wait on a model.

Script v3 (2026-10-27). The words spoken are `scripts/video/narration.json`, voiced by
`scripts/video/narrate.py` and timed to the captions on screen; read that file, not a copy here,
to record a human voice instead. Numbers on screen are real: read them off the pages on the day.
Dry run in demo mode: 2:35 with 350 words of voice; the sandbox checkout adds about 15 s.

| Beat | On screen | Criterion |
|---|---|---|
| Problem (7 s) | One card: book six has to agree with book one | Impact |
| Holmes (≈46 s) | The sample report's triage grid (AG Grid) → Watson's card → the landlady → the Reichenbach clearance → the bible explorer, one column per book → a click on the chart's *Return* bar (AG Charts) | Technology, Design |
| Buying (≈40 s) | Home → demo series → quote with the arithmetic → PayPal's card pop-up, as a guest with the sandbox test card (played at 1.7×) → job page → report | PayPal, Design |
| Assistant (≈63 s) | `agent-video.webm`: a real headless agent on Preflight's MCP endpoint, every call and reply its own, in an unbranded chat panel (the rules bar third-party trademarks in the video); a $20 budget the person approves, an audit and a draft check paid from it, a finding the person marks wrong (the agent only gets a link), the budget closed; ends on the budget's ledger | Agentic commerce, Innovation |
| Honest (7 s) | The benchmark numbers from the README's latest row | Technology |
| Close (5 s) | Repo and hosted URLs | — |

## Production notes
- Caches warm: `audit`, `check` and the Saltmarsh job must complete in seconds on camera. Run
  each once the day before; a re-run replays from `.canonkeeper/cache`.
- No PayPal login on camera: checkout is PayPal's guest card form with the sandbox test card.
- The assistant beat is the real session's film: no branded assistant UI on screen.
- Never show `.env`, a real manuscript, a pen name, or anything under `drafts/`.
- Keep the PayPal checkout on screen only as long as the approval takes; the product is the
  report, not the payment page.

## The cut, generated

`scripts/video/agent_session.py` runs the assistant beat for real: a headless agent connected
to the server's MCP endpoint, asked five things an author would ask. The one step it can never
take, approving a PayPal payment, the script takes as the person would. It saves the transcript
and films it twice: `agent.webm` (named, for /assistant and the README) and `agent-video.webm`
(the generic title and shorter holds the video needs).

`scripts/video/record.py` films every other beat from the running app with captions drawn into
the page, and takes `--agent-clip` for the assistant beat. Against a server with PayPal
**sandbox** keys the payment is real: the recorder pays through PayPal's own card button as a
guest with PayPal's published sandbox test card (no PayPal login, local server only), films the
pop-up and splices it in where it opened; the cut plays it at 1.7×. Without sandbox keys the
same scenes run on the simulated checkout. `scripts/video/assemble.py` fades and joins the
scenes into `preflight.mp4` (1280×720, silent; narration is added over it) and warns at 3:00.

```bash
set -a; . keys.env; set +a           # sandbox keys only
canonkeeper serve --provider mock --port 8791 --workdir build/w &
python scripts/video/agent_session.py --base http://127.0.0.1:8791 --out build/agent
python scripts/video/record.py --base http://127.0.0.1:8791 --out build/video \
    --agent-clip build/agent/agent-video.webm
python scripts/video/assemble.py build/video
python scripts/video/narrate.py build/video --piper <piper venv>/bin/piper \
    --model en_US-libritts_r-medium.onnx  # -> preflight-narrated.mp4 and narration.srt
```

The voice is Piper (GPL, run as a separate program; not a dependency) with the LibriTTS-R voice
(CC BY 4.0, credited in the YouTube description). `narrate.py` warns when a line would start
late or run past the end; shorten the line in narration.json, not the picture. Upload
`narration.srt` to YouTube as the English subtitles.

Devpost testing notes: if judges should try the hosted sandbox checkout, put the sandbox buyer's
email and password in Devpost's private testing instructions, never in the repo.
