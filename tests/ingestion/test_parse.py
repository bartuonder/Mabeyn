import pytest

from mabeyn.db.models import DocumentSource
from mabeyn.ingestion.parse import (
    ParseError,
    detect_language,
    parse_document,
    parse_html,
    parse_markdown,
    parse_pdf,
    split_front_matter,
)

MARKDOWN = """---
title: Return Policy
language: en
effective_date: 2025-01-01
---
# Return Policy

Intro text.

## Eligibility

Items can be returned within 30 days.

### Exceptions

Final sale items cannot be returned.

## Process

```
# not a heading
```

Open a ticket.
"""


def test_front_matter_is_split_and_lowercased():
    metadata, body = split_front_matter(MARKDOWN)
    assert metadata == {"title": "Return Policy", "language": "en", "effective_date": "2025-01-01"}
    assert body.startswith("# Return Policy")


def test_markdown_sections_keep_heading_path():
    parsed = parse_markdown(MARKDOWN)
    paths = [s.path for s in parsed.sections]
    assert paths == [
        ("Return Policy",),
        ("Return Policy", "Eligibility"),
        ("Return Policy", "Eligibility", "Exceptions"),
        ("Return Policy", "Process"),
    ]
    assert parsed.title == "Return Policy"


def test_markdown_ignores_headings_inside_code_fences():
    parsed = parse_markdown(MARKDOWN)
    process = parsed.sections[-1]
    assert "# not a heading" in process.text


def test_markdown_title_falls_back_to_first_h1():
    assert parse_markdown("# Hello\n\ntext").title == "Hello"
    assert parse_markdown("no heading at all").title is None


def test_html_sections_and_noise_removal():
    html = """<html><head><title>Shipping FAQ</title><style>p{}</style></head>
    <body><nav>menu</nav><h1>Shipping</h1><p>We ship daily.</p>
    <h2>Costs</h2><ul><li>Standard: 30 TL</li><li>Express: 60 TL</li></ul>
    <table><tr><td>Zone</td><td>Days</td></tr></table><script>alert(1)</script></body></html>"""
    parsed = parse_html(html)
    assert parsed.title == "Shipping FAQ"
    assert parsed.sections[0].path == ("Shipping",)
    assert "menu" not in parsed.text and "alert" not in parsed.text
    assert parsed.sections[1].path == ("Shipping", "Costs")
    assert "Standard: 30 TL" in parsed.sections[1].text
    assert "Zone | Days" in parsed.sections[1].text


def make_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        content_id = 4 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents {content_id} 0 R "
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            "/BaseFont /Helvetica >> >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_pdf_pages_become_sections_with_page_numbers():
    parsed = parse_pdf(make_pdf(["First page text", "Second page text"]))
    assert [s.page for s in parsed.sections] == [1, 2]
    assert "First page text" in parsed.sections[0].text
    assert parsed.sections[1].path == ("Page 2",)


def test_garbage_pdf_is_rejected():
    with pytest.raises(ParseError):
        parse_pdf(b"%PDF-1.4 definitely not a pdf")


def test_non_utf8_text_is_rejected():
    with pytest.raises(ParseError, match="UTF-8"):
        parse_document(DocumentSource.MARKDOWN, b"\xff\xfe\x00bad")


def test_empty_document_is_rejected():
    with pytest.raises(ParseError, match="no text"):
        parse_document(DocumentSource.MARKDOWN, b"---\ntitle: x\n---\n")


def test_language_detection():
    assert detect_language("İade işlemi için ürünün faturası ve orijinal ambalajı gerekir.") == "tr"
    assert detect_language("Returns are accepted for items in the original packaging.") == "en"
