from canonkeeper.corpus.chapterize import chapterize
from canonkeeper.corpus.gutenberg import strip_boilerplate


def test_chapter_headings_detected():
    text = (
        "CHAPTER I\n\n"
        + "word " * 500
        + "\n\nCHAPTER II\n\n"
        + "more " * 500
        + "\n\nIII.\n\n"
        + "last " * 500
    )
    chs = chapterize(text, min_chars=100)
    assert [c.title for c in chs] == ["CHAPTER I", "CHAPTER II", "III."]
    assert chs[0].char_start == 0
    assert chs[-1].char_end == len(text)
    # spans tile the text exactly
    assert "".join(c.text for c in chs) == text


def test_tiny_fragment_glued_to_previous():
    text = "CHAPTER I\n\n" + "a " * 1000 + "\n\nPART TWO\n\nCHAPTER II\n\n" + "b " * 1000
    chs = chapterize(text, min_chars=200)
    assert len(chs) == 2
    assert "PART TWO" in chs[0].text


def test_fallback_windows_when_no_headings():
    text = ("para " * 300 + "\n\n") * 10
    chs = chapterize(text, fallback_window=5000)
    assert len(chs) > 1
    assert all(c.title.startswith("Section") for c in chs)
    assert "".join(c.text for c in chs) == text


def test_strip_gutenberg_boilerplate():
    raw = "junk\n*** START OF THE PROJECT GUTENBERG EBOOK FOO ***\nbody text\n*** END OF THE PROJECT GUTENBERG EBOOK FOO ***\nlicence"
    assert strip_boilerplate(raw) == "body text"
