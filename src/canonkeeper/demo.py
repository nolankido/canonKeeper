"""A self-contained, invented three-book series with planted continuity errors.

Used by `canonkeeper demo` to run the whole pipeline with no API key, and as a fixture for the
README screenshots. Every name and place is made up; the prose is written in the sentence shapes
the rule-based mock recognises, so the offline run produces a realistic-looking bible and report.
Against the real models the same corpus works as a smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path

SERIES_NAME = "The Saltmarsh Chronicles"

_PAD = (
    "The tide came in across the flats as it always did, slow and grey and indifferent, and the "
    "gulls followed it. Nobody on the quay looked up. There was a cart to unload and the light "
    "was going.\n\n"
    "Later there would be talk in the Anchor about who had said what to whom, but for now the "
    "town kept its own counsel and the lamps came on one by one along the harbour wall."
)

BOOK1 = f"""CHAPTER I. THE HARBOUR

Ivy Marlowe has green eyes. Ivy Marlowe was born in Portmouth. Ivy Marlowe had red hair. Ivy Marlowe walked to Saltmarsh.

{_PAD}

Tobias Crane had brown eyes. Tobias Crane was born in Kelby. Tobias Crane was shot in the shoulder. Tobias Crane walked to Saltmarsh.

CHAPTER II. THE ANCHOR

Hester Vale was born in Saltmarsh. Hester Vale had grey eyes. Hester Vale was the sister of Ivy Marlowe. Hester Vale walked to Kelby.

{_PAD}

Tobias Crane was known to everyone as Toby. Ivy Marlowe married Tobias Crane.

CHAPTER III. THE LETTER

Ivy Marlowe walked to Kelby. Hester Vale walked to Portmouth. Tobias Crane walked to Portmouth.

{_PAD}
"""

BOOK2 = f"""CHAPTER I. SPRING

Ivy Marlowe has green eyes. Ivy Marlowe walked to Portmouth. Tobias Crane walked to Portmouth.

{_PAD}

Oswin Reed was born in Dunmere. Oswin Reed had black hair. Oswin Reed walked to Saltmarsh.

CHAPTER II. THE WRECK

Hester Vale had grey eyes. Hester Vale walked to Saltmarsh. Oswin Reed died.

{_PAD}

Tobias Crane was wounded in the shoulder. Tobias Crane walked to Saltmarsh.

CHAPTER III. AFTER

Ivy Marlowe walked to Saltmarsh. Hester Vale walked to Dunmere.

{_PAD}
"""

BOOK3 = f"""CHAPTER I. THE RETURN

Ivy Marlowe has blue eyes. Ivy Marlowe walked to Saltmarsh. Oswin Reed walked to Saltmarsh.

{_PAD}

Tobias Crane was known to everyone as Tom. Tobias Crane walked to Kelby.

CHAPTER II. THE CONFESSION

"I was born in Dunmere," said Hester Vale, "and I have never once regretted leaving it."

{_PAD}

Tobias Crane was shot in the leg. Ivy Marlowe walked to Dunmere.

CHAPTER III. THE FLATS

Hester Vale walked to Saltmarsh. Ivy Marlowe walked to Saltmarsh. Tobias Crane walked to Saltmarsh.

{_PAD}
"""

PLANTED = [
    ("Ivy Marlowe", "eye_color", "green → blue in book 3"),
    ("Tobias Crane", "wound_location", "shoulder → leg in book 3"),
    ("Tobias Crane", "first_name", "Toby → Tom in book 3"),
    ("Hester Vale", "birthplace", "Saltmarsh → Dunmere in book 3"),
    ("Oswin Reed", "dead", "dies in book 2, walks in book 3"),
]


def write_demo_series(dest: Path) -> Path:
    """Write the three books and a manifest under `dest`; return the manifest path."""
    books = dest / "books"
    books.mkdir(parents=True, exist_ok=True)
    (books / "01-the-harbour.txt").write_text(BOOK1, encoding="utf-8")
    (books / "02-the-wreck.txt").write_text(BOOK2, encoding="utf-8")
    (books / "03-the-return.txt").write_text(BOOK3, encoding="utf-8")
    manifest = {
        "series": SERIES_NAME,
        "books": [
            {"key": "01-the-harbour", "title": "The Harbour", "path": "books/01-the-harbour.txt"},
            {"key": "02-the-wreck", "title": "The Wreck", "path": "books/02-the-wreck.txt"},
            {"key": "03-the-return", "title": "The Return", "path": "books/03-the-return.txt"},
        ],
    }
    path = dest / "series.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path
