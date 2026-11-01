import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from mabeyn.db.models import DocumentSource

MAX_PDF_PAGES = 500
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HTML_NOISE = ["script", "style", "nav", "footer", "header", "noscript", "form", "aside"]
HTML_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
HTML_BLOCKS = [*HTML_HEADINGS, "p", "li", "pre", "blockquote", "tr"]

TURKISH_WORDS = {"ve", "bir", "için", "ile", "bu", "da", "de", "olarak", "olan", "veya", "daha"}
ENGLISH_WORDS = {"the", "and", "of", "to", "is", "for", "with", "or", "are", "this", "that"}


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]
    text: str
    page: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    sections: list[Section]
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.sections)


def detect_language(text: str) -> str:
    words = re.findall(r"\w+", text.lower())
    turkish = sum(w in TURKISH_WORDS for w in words)
    turkish += sum(any(c in w for c in "çğışöü") for w in words) // 4
    english = sum(w in ENGLISH_WORDS for w in words)
    return "tr" if turkish >= english else "en"


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            metadata[key.strip().lower()] = value.strip().strip("'\"")
    return metadata, text[match.end() :]


class _SectionBuilder:
    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []
        self._lines: list[str] = []
        self.sections: list[Section] = []

    def heading(self, level: int, title: str) -> None:
        self.flush()
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    def add(self, line: str) -> None:
        self._lines.append(line)

    def flush(self) -> None:
        text = "\n".join(self._lines).strip()
        self._lines = []
        if text:
            path = tuple(title for _, title in self._stack)
            self.sections.append(Section(path=path, text=text))


def parse_markdown(raw: str) -> ParsedDocument:
    metadata, body = split_front_matter(raw)
    builder = _SectionBuilder()
    in_fence = False
    first_h1: str | None = None
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2).strip()
            if level == 1 and first_h1 is None:
                first_h1 = title
            builder.heading(level, title)
        else:
            builder.add(line)
    builder.flush()
    return ParsedDocument(
        title=metadata.get("title") or first_h1, sections=builder.sections, metadata=metadata
    )


def parse_html(raw: str) -> ParsedDocument:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(HTML_NOISE):
        tag.decompose()
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    builder = _SectionBuilder()
    root = soup.body or soup
    for element in root.find_all(HTML_BLOCKS):
        if not isinstance(element, Tag) or element.find(HTML_BLOCKS) is not None:
            continue
        if element.name == "tr":
            text = " | ".join(c.get_text(" ", strip=True) for c in element.find_all(["td", "th"]))
        else:
            text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name in HTML_HEADINGS:
            builder.heading(int(element.name[1]), text)
            title = title or text
        else:
            builder.add(text)
    builder.flush()
    return ParsedDocument(title=title, sections=builder.sections)


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ParseError(f"PDF has more than {MAX_PDF_PAGES} pages")
        title = (reader.metadata.title or "").strip() if reader.metadata else ""
        sections = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(Section(path=(f"Page {number}",), text=text, page=number))
    except PyPdfError as exc:
        raise ParseError(f"invalid PDF: {exc}") from exc
    if not sections:
        raise ParseError("PDF contains no extractable text")
    return ParsedDocument(title=title or None, sections=sections)


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("file is not valid UTF-8 text") from exc


def parse_document(source_type: DocumentSource, data: bytes) -> ParsedDocument:
    if source_type is DocumentSource.PDF:
        parsed = parse_pdf(data)
    elif source_type is DocumentSource.MARKDOWN:
        parsed = parse_markdown(decode_text(data))
    else:
        parsed = parse_html(decode_text(data))
    if not parsed.sections:
        raise ParseError("document contains no text")
    return parsed
