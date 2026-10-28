"""Turn the recorder's assistant scene into the short loop the README and /assistant show.

    python scripts/video/clip.py build/video/03-agent.webm

Writes `docs/img/assistant-budget.gif` (README; GitHub plays GIFs inline) and
`src/canonkeeper/preflight/static/assistant-budget.mp4` (the /assistant page, small H.264). The
scene is played at 1.6x: the MP4 720 px wide, the GIF 640 px at 6 frames a second. Needs ffmpeg.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIF = ROOT / "docs/img/assistant-budget.gif"
MP4 = ROOT / "src/canonkeeper/preflight/static/assistant-budget.mp4"
POSTER = MP4.with_suffix(".jpg")  # the frame a page shows before the video plays
SKIP = 1.5  # the film opens on an empty chat panel: start on the first message
POSTER_AT = 0.45  # mid-session: tool calls and a reply on screen
SPEED, WIDTH, FPS = 1.6, 720, 8
GIF_WIDTH, GIF_FPS = 640, 6  # a README GIF under ~3 MB


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def main(scene: str) -> None:
    src = Path(scene)
    speed = f"setpts=PTS/{SPEED}"
    poster_t = POSTER_AT * float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(src),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    run(
        "-ss",
        f"{poster_t:.1f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-vf",
        f"scale={WIDTH}:-2:flags=lanczos",
        "-q:v",
        "4",
        str(POSTER),
    )
    run(
        "-ss", str(SKIP), "-i", str(src), "-vf", f"{speed},scale={WIDTH}:-2:flags=lanczos,fps=24",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-an", str(MP4),
    )  # fmt: skip
    palette = GIF.with_suffix(".palette.png")
    base = f"{speed},fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos"
    run("-ss", str(SKIP), "-i", str(src), "-vf", f"{base},palettegen=max_colors=96", str(palette))
    run(
        "-ss", str(SKIP), "-i", str(src), "-i", str(palette),
        "-lavfi", f"{base}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", str(GIF),
    )  # fmt: skip
    palette.unlink()
    for p in (GIF, MP4, POSTER):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1])
