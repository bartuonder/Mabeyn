from mabeyn.ingestion.chunk import ChunkConfig, chunk_document, count_tokens
from mabeyn.ingestion.parse import ParsedDocument, Section, parse_markdown


def words(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def doc(*sections: Section) -> ParsedDocument:
    return ParsedDocument(title="Doc", sections=list(sections))


def test_small_sections_are_merged_until_min_tokens():
    sections = [Section(("Policy", f"Part {i}"), words(40, f"p{i}x")) for i in range(10)]
    chunks = chunk_document(doc(*sections), "Doc", ChunkConfig(min_tokens=300, max_tokens=700))
    assert len(chunks) < 10
    assert all(c.token_count <= 700 for c in chunks)
    assert chunks[0].section.count(" | ") >= 1
    assert "## Part 0" in chunks[0].content


def test_chunks_cut_at_section_boundary_after_min_tokens():
    big = Section(("A",), words(150, "a"))
    other = Section(("B",), words(150, "b"))
    chunks = chunk_document(doc(big, other), "Doc", ChunkConfig(min_tokens=100, max_tokens=1000))
    assert [c.section for c in chunks] == ["A", "B"]


def test_no_chunk_exceeds_max_tokens_and_no_text_is_lost():
    long_paragraph = ". ".join(words(30, f"s{i}x") for i in range(40)) + "."
    parsed = doc(Section(("Long",), long_paragraph), Section(("Short",), "tiny section"))
    chunks = chunk_document(parsed, "Doc", ChunkConfig(min_tokens=100, max_tokens=200))
    assert len(chunks) > 3
    assert all(c.token_count <= 200 + 5 for c in chunks)
    joined = " ".join(c.content for c in chunks)
    for i in range(40):
        assert f"s{i}x0" in joined
    assert "tiny section" in joined


def test_unbroken_text_is_hard_split():
    parsed = doc(Section(("Blob",), "x" * 5000))
    chunks = chunk_document(parsed, "Doc", ChunkConfig(min_tokens=50, max_tokens=100))
    assert len(chunks) > 1
    assert all(c.token_count <= 105 for c in chunks)


def test_context_header_and_metadata():
    parsed = doc(Section(("Returns", "Eligibility"), "Thirty days.", page=3))
    (chunk,) = chunk_document(parsed, "Return Policy")
    assert chunk.context_header == "Return Policy > Returns > Eligibility"
    assert chunk.section == "Returns > Eligibility"
    assert chunk.page == 3
    assert chunk.position == 0
    assert chunk.embedding_text.startswith(chunk.context_header + "\n")


def test_positions_are_sequential_and_output_is_deterministic():
    parsed = parse_markdown("\n\n".join(f"## S{i}\n\n{words(80, f'w{i}x')}" for i in range(8)))
    config = ChunkConfig(min_tokens=100, max_tokens=250)
    first = chunk_document(parsed, "Doc", config)
    second = chunk_document(parsed, "Doc", config)
    assert first == second
    assert [c.position for c in first] == list(range(len(first)))


def test_empty_document_has_no_chunks():
    assert chunk_document(doc(), "Doc") == []


def test_count_tokens_is_positive_for_text():
    assert count_tokens("hello world") >= 2
