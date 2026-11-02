import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from mabeyn.ingestion.parse import ParsedDocument

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
PATH_SEPARATOR = " > "
SECTION_SEPARATOR = " | "


@dataclass(frozen=True)
class ChunkConfig:
    min_tokens: int = 300
    max_tokens: int = 600


@dataclass(frozen=True)
class DraftChunk:
    position: int
    content: str
    context_header: str
    section: str
    page: int | None
    token_count: int

    @property
    def embedding_text(self) -> str:
        return f"{self.context_header}\n{self.content}"


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def truncate_tokens(text: str, max_tokens: int) -> str:
    tokens = _encoding().encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _encoding().decode(tokens[:max_tokens])


@dataclass(frozen=True)
class _Block:
    path: tuple[str, ...]
    text: str
    page: int | None
    starts_section: bool


def _hard_split(text: str, max_tokens: int) -> list[str]:
    tokens = _encoding().encode(text)
    return [
        _encoding().decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens)
    ]


def _split_oversize(paragraph: str, max_tokens: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in SENTENCE_RE.split(paragraph):
        if count_tokens(sentence) > max_tokens:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_split(sentence, max_tokens))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _blocks(parsed: ParsedDocument, max_tokens: int) -> list[_Block]:
    blocks: list[_Block] = []
    for section in parsed.sections:
        first = True
        for paragraph in re.split(r"\n\s*\n", section.text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            pieces = (
                [paragraph]
                if count_tokens(paragraph) <= max_tokens
                else _split_oversize(paragraph, max_tokens)
            )
            for piece in pieces:
                blocks.append(_Block(section.path, piece, section.page, first))
                first = False
    return blocks


def _render(blocks: list[_Block]) -> str:
    lines: list[str] = []
    previous: tuple[str, ...] | None = None
    for block in blocks:
        heading_needed = block.path and (block.starts_section or previous is None)
        if heading_needed and block.path != previous:
            lines.append(f"## {block.path[-1]}")
        lines.append(block.text)
        previous = block.path
    return "\n\n".join(lines)


def _label(blocks: list[_Block]) -> str:
    seen: list[str] = []
    for block in blocks:
        label = PATH_SEPARATOR.join(block.path) or "Document"
        if label not in seen:
            seen.append(label)
    return SECTION_SEPARATOR.join(seen)


def chunk_document(
    parsed: ParsedDocument, title: str, config: ChunkConfig | None = None
) -> list[DraftChunk]:
    """Pack blocks into chunks, cutting at section boundaries once `min_tokens` is reached."""
    config = config or ChunkConfig()
    groups: list[list[_Block]] = []
    current: list[_Block] = []
    current_tokens = 0
    for block in _blocks(parsed, config.max_tokens):
        size = count_tokens(block.text)
        over_budget = current and current_tokens + size > config.max_tokens
        boundary = current and block.starts_section and current_tokens >= config.min_tokens
        if over_budget or boundary:
            groups.append(current)
            current, current_tokens = [], 0
        current.append(block)
        current_tokens += size
    if current:
        groups.append(current)

    chunks: list[DraftChunk] = []
    for position, group in enumerate(groups):
        content = _render(group)
        label = _label(group)
        first_path = PATH_SEPARATOR.join(group[0].path)
        header = f"{title}{PATH_SEPARATOR}{first_path}" if first_path else title
        chunks.append(
            DraftChunk(
                position=position,
                content=content,
                context_header=header,
                section=label,
                page=group[0].page,
                token_count=count_tokens(content),
            )
        )
    return chunks
