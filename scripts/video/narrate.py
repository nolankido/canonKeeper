"""Lay a spoken voice-over on the assembled cut, each line timed to the caption it belongs to.

    python scripts/video/narrate.py build/video --piper <venv>/bin/piper \\
        --model voices/en_US-libritts_r-medium.onnx

The words are in `scripts/video/narration.json`: each line names a cue (or a list of them,
the first that appears wins) (the start of a caption
the recorder drew, `scene:<name>`, or for the assistant's film `turn:<n>`, `call:<tool>`,
`then:<n>`, `budget`) and is spoken from that moment, or as soon as the line before it ends.
Lines are matched in order, so a cue that appears twice (two payments) is taken the second time
by the second line that names it. record.py writes `cues.json`, assemble.py `timeline.json`;
here they become one list of times in the cut, so the voice follows the picture however long
PayPal's pop-up took on the day.

The voice is synthesized offline with Piper (https://github.com/OHF-Voice/piper1-gpl), run as a
separate program: `pip install piper-tts` in its own environment (it is not a dependency of
canonkeeper). The default voice is LibriTTS-R (CC BY 4.0: credit it in the video's
description). Writes `preflight-narrated.mp4` (the voice, normalized to -16 LUFS) and
`narration.srt` (the same words as subtitles, for YouTube). Prints each line's slot and warns
when a line has to wait more than a beat for the one before it, or runs past the end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAP = 0.25  # seconds of quiet between two lines
LATE = 1.5  # warn when a line starts this much after its cue
# How the voice should say a word it gets wrong: the text on screen and in the SRT is unchanged.
SAY = {"Preflight": "Pree flight", "MCP": "M C P"}


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def cue_times(folder: Path) -> list[tuple[float, str]]:
    """Every cue, as seconds into the assembled cut, in order."""
    cues = json.loads((folder / "cues.json").read_text(encoding="utf-8"))
    timeline = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
    out = []
    for c in cues:
        scene = timeline.get(c["scene"])
        if scene is None:
            continue  # a scene the cut left out
        t = scene["start"] + max(0.0, c["t"] - scene["skip"]) / scene["speed"]
        out.append((round(t, 2), c["text"]))
    return sorted(out, key=lambda x: x[0])


def speak(text: str, dest: Path, piper: str, model: Path, speaker: int, scale: float) -> Path:
    said = text
    for word, sound in SAY.items():
        said = said.replace(word, sound)
    key = hashlib.sha256(f"{said}|{model.name}|{speaker}|{scale}".encode()).hexdigest()[:16]
    wav = dest / f"{key}.wav"
    if not wav.exists():
        args = [piper, "-m", str(model), "-f", str(wav), "--length-scale", str(scale)]
        if speaker >= 0:
            args += ["-s", str(speaker)]
        subprocess.run(args, input=said, text=True, check=True, capture_output=True)
    return wav


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path, nargs="?", default=Path("build/video"))
    ap.add_argument("--script", type=Path, default=HERE / "narration.json")
    ap.add_argument("--piper", default="piper", help="the piper program")
    ap.add_argument("--model", type=Path, required=True, help="a Piper voice (.onnx)")
    ap.add_argument("--speaker", type=int, default=120, help="for a multi-speaker voice; -1: none")
    ap.add_argument("--length-scale", type=float, default=1.35, help=">1 speaks slower")
    a = ap.parse_args()

    video = a.folder / "preflight.mp4"
    total = duration(video)
    cues = cue_times(a.folder)
    lines = json.loads(a.script.read_text(encoding="utf-8"))
    voice = a.folder / "narration"
    voice.mkdir(exist_ok=True)

    placed: list[tuple[float, float, str, Path]] = []
    seek, free, problems = 0, 0.0, 0
    for line in lines:
        # a cue may be a list: the first that appears (the sandbox and simulated checkouts
        # caption different moments)
        wants = line["cue"] if isinstance(line["cue"], list) else [line["cue"]]
        hit = next(
            (i for i in range(seek, len(cues)) if any(cues[i][1].startswith(w) for w in wants)),
            None,
        )
        if hit is None:
            after = repr(cues[seek - 1][1]) if seek else "the start"
            raise SystemExit(f"no cue {line['cue']!r} after {after} (see cues.json)")
        seek = hit + 1
        wanted = cues[hit][0] + float(line.get("after", 0.2))
        wav = speak(line["text"], voice, a.piper, a.model, a.speaker, a.length_scale)
        start = max(wanted, free)
        end = start + duration(wav)
        flag = ""
        if start - wanted > LATE:
            flag, problems = (
                f"  <- {start - wanted:.1f}s late: shorten the line before",
                problems + 1,
            )
        if end > total:
            flag, problems = f"  <- runs {end - total:.1f}s past the end", problems + 1
        print(f"{start:6.1f}-{end:6.1f}  {line['text'][:70]}{flag}")
        placed.append((start, end, line["text"], wav))
        free = end + GAP

    words = sum(len(t.split()) for _, _, t, _ in placed)
    spoken = sum(e - s for s, e, _, _ in placed)
    print(f"{len(placed)} lines, {words} words, {spoken:.0f}s of voice in {total:.0f}s of video")

    (a.folder / "narration.srt").write_text(
        "".join(
            f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n"
            for i, (s, e, t, _) in enumerate(placed, 1)
        ),
        encoding="utf-8",
    )
    inputs: list[str] = []
    chains = []
    for i, (s, _, _, wav) in enumerate(placed):
        inputs += ["-i", str(wav)]
        ms = int(s * 1000)
        chains.append(f"[{i + 1}:a]aresample=48000,adelay={ms}|{ms}[v{i}]")
    mix = "".join(f"[v{i}]" for i in range(len(placed)))
    graph = ";".join(chains) + (
        f";{mix}amix=inputs={len(placed)}:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=0:{total:.2f}[voice]"
    )
    out = a.folder / "preflight-narrated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *inputs,
         "-filter_complex", graph, "-map", "0:v", "-map", "[voice]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ac", "2",
         "-movflags", "+faststart", str(out)],
        check=True,
    )  # fmt: skip
    print(
        f"-> {out}  ({duration(out):.1f}s)" + (f"  {problems} line(s) to fix" if problems else "")
    )


if __name__ == "__main__":
    main()
