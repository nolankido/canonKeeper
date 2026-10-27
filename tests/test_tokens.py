from canonkeeper.tokens import count_tokens, split_by_tokens


def _check(text: str, limit: int) -> list[tuple[str, int]]:
    pieces = split_by_tokens(text, limit)
    for piece, off in pieces:
        assert text[off : off + len(piece)] == piece  # offsets are exact
    # nothing lost or duplicated: pieces tile the text, separators aside
    joined = "".join(p for p, _ in pieces)
    assert joined.replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")
    return pieces


def test_paragraphs_are_packed_and_never_split_when_they_fit():
    paras = [f"Paragraph {i} says something about the harbour and the tide." for i in range(60)]
    text = "\n\n".join(paras)
    pieces = _check(text, 100)
    assert len(pieces) > 1
    assert all(p.startswith("Paragraph") and p.endswith("tide.") for p, _ in pieces)
    assert split_by_tokens("short", 100) == [("short", 0)]


def test_a_chapter_with_single_newlines_is_still_cut_to_size():
    """Word exports often put one newline between paragraphs: the whole chapter is one
    "paragraph". It used to come back as a single oversized chunk."""
    lines = [
        f"Ivy Marlowe walked to the quay for the {i}th time and looked at the sea."
        for i in range(400)
    ]
    text = "\n".join(lines)
    assert count_tokens(text) > 3500
    pieces = _check(text, 500)
    assert len(pieces) >= 8
    assert all(count_tokens(p) <= 520 for p, _ in pieces)
    assert all(p.startswith("Ivy") for p, _ in pieces)  # cut at line breaks, not mid-line


def test_one_enormous_line_is_cut_at_sentences_then_words():
    sentences = " ".join(f"Tobias Crane counted barrel number {i} twice." for i in range(300))
    pieces = _check(sentences, 300)
    assert all(count_tokens(p) <= 320 for p, _ in pieces)
    assert all(p.rstrip().endswith(".") for p, _ in pieces[:-1])  # sentence boundaries
    words = " ".join(["saltmarsh"] * 3000)  # no sentence ends at all
    assert all(count_tokens(p) <= 320 for p, _ in _check(words, 300))
