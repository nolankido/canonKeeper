"""Text colours pass WCAG AA (4.5:1) on every background they are drawn on. axe-core found the
amber warning text at 3.45-4.24; scripts/a11y_check.py runs the full check in a browser."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "canonkeeper"
TEMPLATES = [
    ROOT / "preflight/templates/base.html",
    ROOT / "report/templates/report.html",
    ROOT / "report/templates/bible.html",
    ROOT / "report/templates/bible_grid.html",
]
BACKGROUNDS = ["#ffffff", "#faf8f3", "#f1e7d6", "#fbf1e1"]  # card, page, banner, disputed row


def _lum(h: str) -> float:
    h = h.lstrip("#")
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted([_lum(a), _lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_light_theme_text_colours_meet_aa_on_every_background():
    for t in TEMPLATES:
        light = t.read_text(encoding="utf-8").split("@media (prefers-color-scheme: dark)")[0]
        for name in ("warn", "med", "hi", "bad", "accent", "muted", "ok"):
            for colour in re.findall(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", light):
                worst = min(_ratio(colour, bg) for bg in BACKGROUNDS)
                assert worst >= 4.5, f"{t.name} --{name} {colour}: {worst:.2f}"
