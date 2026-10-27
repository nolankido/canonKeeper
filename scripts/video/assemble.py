"""Encode and join the scenes recorded by record.py into one MP4 (H.264, 30 fps, 1280x720).

    python scripts/video/assemble.py build/video
    python scripts/video/assemble.py build/video --replace buy=build/video/checkout.mp4

`--replace scene=clip` swaps a recorded scene for any other recording (the real PayPal sandbox
checkout, captured with Win+Alt+R at whatever size): it is scaled and letterboxed to 1280x720,
gets the same fades, and its own audio is dropped for the silent track. The total must stay
under three minutes for Devpost; the script says so if it does not.

Each scene is trimmed of the blank frames Playwright records before the first paint, gets a
short fade in and out, and the result is concatenated. A silent audio track is added so
YouTube and editors treat it as a normal video; record a voiceover over it if you like.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

TRIM_START = 0.5  # seconds of blank page at the start of every Playwright recording
FADE = 0.35


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


LIMIT_S = 180.0  # Devpost: under three minutes


def main(folder: Path, replace: dict[str, Path] | None = None) -> Path:
    replace = replace or {}
    order = json.loads((folder / "order.json").read_text(encoding="utf-8"))
    unknown = set(replace) - set(order)
    if unknown:
        raise SystemExit(
            f"no such scene: {', '.join(sorted(unknown))} (scenes: {', '.join(order)})"
        )
    clips = []
    for i, name in enumerate(order):
        external = replace.get(name)
        src = external or folder / f"{i:02d}-{name}.webm"
        dst = folder / f"{i:02d}-{name}.mp4"
        skip = 0.0 if external else TRIM_START
        d = duration(src) - skip
        fit = (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xfaf8f3,setsar=1,"
            if external
            else ""
        )
        vf = (
            f"{fit}fps=30,format=yuv420p,fade=t=in:st=0:d={FADE},"
            f"fade=t=out:st={max(0.0, d - FADE):.2f}:d={FADE}"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                str(skip),
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:a",
                "aac",
                "-shortest",
                str(dst),
            ],
            check=True,
        )
        clips.append(dst)
        print(f"{name:10} {d:6.1f}s{'  (replaced: ' + str(external) + ')' if external else ''}")
    lst = folder / "concat.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    final = folder / "preflight.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final),
        ],
        check=True,
    )
    total = duration(final)
    print(f"total  {total:6.1f}s -> {final}")
    if total >= LIMIT_S:
        print(f"WARNING: {total:.0f}s is over Devpost's three-minute limit; trim a scene")
    return final


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="build/video", type=Path)
    ap.add_argument("--replace", action="append", default=[], metavar="SCENE=CLIP")
    a = ap.parse_args()
    swaps = {k: Path(v) for k, v in (r.split("=", 1) for r in a.replace)}
    main(a.folder, swaps)
