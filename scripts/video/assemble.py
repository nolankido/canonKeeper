"""Encode and join the scenes recorded by record.py into one MP4 (H.264, 30 fps, 1280x720).

    python scripts/video/assemble.py build/video

Each scene is trimmed of the blank frames Playwright records before the first paint, gets a
short fade in and out, and the result is concatenated. A silent audio track is added so
YouTube and editors treat it as a normal video; record a voiceover over it if you like.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def main(folder: Path) -> None:
    order = json.loads((folder / "order.json").read_text(encoding="utf-8"))
    clips = []
    for i, name in enumerate(order):
        src = folder / f"{i:02d}-{name}.webm"
        dst = folder / f"{i:02d}-{name}.mp4"
        d = duration(src) - TRIM_START
        vf = (
            f"fps=30,format=yuv420p,fade=t=in:st=0:d={FADE},"
            f"fade=t=out:st={max(0.0, d - FADE):.2f}:d={FADE}"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                str(TRIM_START),
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
                "-c:a",
                "aac",
                "-shortest",
                str(dst),
            ],
            check=True,
        )
        clips.append(dst)
        print(f"{name:10} {d:6.1f}s")
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
    print(f"total  {duration(final):6.1f}s -> {final}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/video"))
