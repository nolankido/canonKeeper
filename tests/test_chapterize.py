"""Chapterizer tests. Each fixture mimics the exact layout of one Project Gutenberg Sherlock
Holmes volume (headings, contents block, stray all-caps lines), with filler bodies."""

from pathlib import Path

import pytest

from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.gutenberg import strip_boilerplate

BODY = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 6 + "\n\n") * 4


def test_study_in_scarlet_layout_parts_contents_and_split_titles():
    text = (
        "A STUDY IN SCARLET\n\nBy A. Conan Doyle\n\n\nCONTENTS\n\n A STUDY IN SCARLET.\n\n PART I.\n"
        " CHAPTER I. MR. SHERLOCK HOLMES.\n CHAPTER II. THE SCIENCE OF DEDUCTION.\n\n"
        " PART II. THE COUNTRY OF THE SAINTS\n CHAPTER I. ON THE GREAT ALKALI PLAIN.\n"
        " CHAPTER II. THE FLOWER OF UTAH.\n\n\n\n\nA STUDY IN SCARLET.\n\n\n\n\nPART I.\n\n\n"
        "(_Being a reprint from the Reminiscences of_ JOHN H. WATSON, M.D.)\n\n\n\n\n"
        "CHAPTER I.\nMR. SHERLOCK HOLMES.\n\n\n"
        + BODY
        + "\n\nRACHE.\n\n"
        + BODY
        + "\n\n\nCHAPTER II.\nTHE SCIENCE OF DEDUCTION.\n\n\n"
        + BODY
        + "\n\n\nPART II.\n\n\nCHAPTER I.\nON THE GREAT ALKALI PLAIN.\n\n\n"
        + BODY
        + "\n\n\nCHAPTER II.\nTHE FLOWER OF UTAH.\n\n\n"
        + BODY
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "CHAPTER I. MR. SHERLOCK HOLMES.",
        "CHAPTER II. THE SCIENCE OF DEDUCTION.",
        "CHAPTER I. ON THE GREAT ALKALI PLAIN.",
        "CHAPTER II. THE FLOWER OF UTAH.",
    ]
    assert "RACHE." in chs[0].text  # shouted body line did not split the chapter
    assert "CONTENTS" not in "".join(c.text for c in chs)  # front matter excluded
    assert chs[-1].char_end == len(text)
    for c in chs:
        assert text[c.char_start : c.char_end] == c.text


def test_sign_of_four_layout_title_on_next_line_no_period():
    text = (
        "The Sign of the Four\n\nby Arthur Conan Doyle\n\n\nContents\n\n"
        "   Chapter I. The Science of Deduction\n   Chapter II. The Statement of the Case\n"
        "   Chapter III. In Quest of a Solution\n\n\n\n\nChapter I\nThe Science of Deduction\n\n\n"
        + BODY
        + "\n\nI nodded, to show that I followed his reasoning.\n\n"
        + BODY
        + "\n\n\nChapter II\nThe Statement of the Case\n\n\n"
        + BODY
        + "\n\n\nChapter III\nIn Quest of a Solution\n\n\n"
        + BODY
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "Chapter I The Science of Deduction",
        "Chapter II The Statement of the Case",
        "Chapter III In Quest of a Solution",
    ]


def test_hound_layout_arabic_numbers_with_period():
    text = (
        "The Hound of the Baskervilles\n\n\nContents\n\n Chapter 1  Mr. Sherlock Holmes\n"
        " Chapter 2  The Curse of the Baskervilles\n Chapter 3  The Problem\n\n\n\n\n"
        "Chapter 1.\nMr. Sherlock Holmes\n\n\n"
        + BODY
        + "\n\n\nChapter 2.\nThe Curse of the Baskervilles\n\n\n"
        + BODY
        + "\n\n\nChapter 3.\nThe Problem\n\n\n"
        + BODY
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "Chapter 1. Mr. Sherlock Holmes",
        "Chapter 2. The Curse of the Baskervilles",
        "Chapter 3. The Problem",
    ]


def test_adventures_layout_numbered_titles_with_bare_subparts():
    text = (
        "The Adventures of Sherlock Holmes\n\n\nContents\n\n   I.     A Scandal in Bohemia\n"
        "   II.    The Red-Headed League\n   III.   A Case of Identity\n\n\n\n\n"
        "I. A SCANDAL IN BOHEMIA\n\n\nI.\n\n"
        + BODY
        + "\n\nII.\n\n"
        + BODY
        + "\n\n“IRENE NORTON, _née_ ADLER.”\n\n\n\nII. THE RED-HEADED LEAGUE\n\n\n"
        + BODY
        + "\n\n\nIII. A CASE OF IDENTITY\n\n\n"
        + BODY
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "I. A SCANDAL IN BOHEMIA",
        "II. THE RED-HEADED LEAGUE",
        "III. A CASE OF IDENTITY",
    ]
    assert "\nII.\n" in chs[0].text  # bare sub-part numerals stay inside the story


def test_return_layout_unnumbered_caps_titles_from_contents():
    text = (
        "The Return of Sherlock Holmes\n\n\nContents\n\n The Adventure of the Empty House\n"
        " The Adventure of Black Peter.\n The Adventure of the Second Stain\n\n\n\n\n"
        "THE ADVENTURE OF THE EMPTY HOUSE\n\n\n"
        + BODY
        + "\n\n\nTHE ADVENTURE OF BLACK PETER\n\n\n"
        + BODY
        + "\n\n\nTHE ADVENTURE OF THE SECOND STAIN\n\n\n"
        + BODY
        + "\n\n\nTHE END\n"
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "THE ADVENTURE OF THE EMPTY HOUSE",
        "THE ADVENTURE OF BLACK PETER",
        "THE ADVENTURE OF THE SECOND STAIN",
    ]
    assert "THE END" in chs[-1].text


def test_no_contents_falls_back_to_heading_family():
    text = "CHAPTER I\n\n" + BODY + "\n\nCHAPTER II\n\n" + BODY + "\n\nIII.\n\n" + BODY
    chs = chapterize(text, min_chars=100)
    # The CHAPTER family wins; the lone "III." is not of that family and stays in chapter II.
    assert [c.title for c in chs] == ["CHAPTER I", "CHAPTER II"]
    assert "III." in chs[1].text


def test_part_prefix_without_contents():
    text = (
        "PART I.\n\nCHAPTER I.\nTHE START\n\n"
        + BODY
        + "\n\nPART II.\n\nCHAPTER I.\nTHE MIDDLE\n\n"
        + BODY
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == [
        "Part I · CHAPTER I. THE START",
        "Part II · CHAPTER I. THE MIDDLE",
    ]


def test_tiny_fragment_glued_to_previous():
    text = (
        "CHAPTER I\n\n" + "a " * 1000 + "\n\nCHAPTER II\n\nshort\n\nCHAPTER III\n\n" + "b " * 1000
    )
    chs = chapterize(text, min_chars=200)
    assert [c.title for c in chs] == ["CHAPTER I", "CHAPTER III"]
    assert "short" in chs[0].text


def test_fallback_windows_when_no_headings():
    text = ("para " * 300 + "\n\n") * 10
    chs = chapterize(text, fallback_window=5000)
    assert len(chs) > 1
    assert all(c.title.startswith("Section") for c in chs)
    assert "".join(c.text for c in chs) == text


def test_strip_gutenberg_boilerplate():
    raw = (
        "junk\n*** START OF THE PROJECT GUTENBERG EBOOK FOO ***\nbody text\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK FOO ***\nlicence"
    )
    assert strip_boilerplate(raw) == "body text"


_GUTENBERG = Path(".canonkeeper/cache/gutenberg")
_REAL = [(244, 14), (2097, 12), (1661, 12), (834, 12), (2852, 15), (108, 13)]


@pytest.mark.skipif(
    not _GUTENBERG.exists(), reason="run `canonkeeper fetch` on the Holmes manifest first"
)
@pytest.mark.parametrize("pg,expected", _REAL)
def test_real_gutenberg_files_split_into_the_right_number_of_chapters(pg, expected):
    p = _GUTENBERG / f"pg{pg}.txt"
    if not p.exists():
        pytest.skip(f"{p} not cached")
    chs = chapterize(p.read_text(encoding="utf-8"))
    assert len(chs) == expected, [c.title for c in chs]
    # chapters tile the body exactly and none is suspiciously huge
    for a, b in zip(chs, chs[1:], strict=False):
        assert a.char_end == b.char_start
    assert max(len(c.text) for c in chs) < 120_000


def test_markdown_and_bare_number_headings_are_chapters():
    body = "\n\n".join(["Ivy walked to the quay and watched the boats come in. " * 30] * 3)

    def book(head):
        return "# My Novel\n\nby Someone\n\n" + "\n\n".join(
            head(i) + "\n\n" + body for i in range(1, 5)
        )

    titles = lambda text: [c.title for c in chapterize(text)]  # noqa: E731
    assert titles(book(lambda i: f"# Chapter {i}")) == [f"Chapter {i}" for i in range(1, 5)]
    assert titles(book(lambda i: f"## **The Tide, part {i}**")) == [
        f"The Tide, part {i}" for i in range(1, 5)
    ]
    assert titles(book(lambda i: str(i))) == [f"Chapter {i}" for i in range(1, 5)]
    assert titles(book(lambda i: ["I", "II", "III", "IV"][i - 1] + ".")) == [
        "Chapter I",
        "Chapter II",
        "Chapter III",
        "Chapter IV",
    ]
    # a number standing alone in the text is not a chapter run
    stray = "Chapter text goes on. " * 300 + "\n\n42\n\n" + "More text. " * 300
    assert [c.title for c in chapterize(stray)][0].startswith("Section")


def test_short_chapters_keep_their_own_boundaries():
    """A fact in chapter II is cited to chapter II even when chapters are a few lines long; only
    a near-empty segment (a part title) is folded into the chapter before it."""
    para = "Ivy Marlowe walked to Saltmarsh. " * 12
    text = (
        f"CHAPTER I. THE HARBOUR\n\n{para}\n\nCHAPTER II. THE ANCHOR\n\n{para}\n\n"
        f"PART TWO\n\nCHAPTER III. THE LETTER\n\n{para}\n"
    )
    titles = [c.title for c in chapterize(text)]
    assert len(titles) == 3 and titles[:2] == ["CHAPTER I. THE HARBOUR", "CHAPTER II. THE ANCHOR"]
    assert titles[2].endswith("CHAPTER III. THE LETTER")  # the part title is not a chapter
