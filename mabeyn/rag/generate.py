import re
from collections.abc import AsyncIterator, Sequence

from mabeyn.core.config import get_settings
from mabeyn.llm.client import LLM, ChatMessage
from mabeyn.rag import prompts
from mabeyn.rag.types import Citation, RetrievedChunk

MAX_HISTORY_MESSAGES = 6
MAX_ANSWER_TOKENS = 700
TAG_RE = re.compile(r"\[([^\[\]]+:[^\[\]]+)\]")


FORGED_TAG = re.compile(r"<(/?\s*sources?\b)", re.IGNORECASE)


def neutralise(text: str) -> str:
    """Spotlighting: retrieved text may not close or reopen the data block it sits in."""
    return FORGED_TAG.sub(lambda m: "\u2039" + m.group(1), text).replace('"', "'")


def format_sources(chunks: Sequence[RetrievedChunk]) -> str:
    parts = []
    for chunk in chunks:
        date_attr = f' effective_date="{chunk.effective_date}"' if chunk.effective_date else ""
        tags = " ".join(f"[{tag}]" for tag in chunk.cites)
        parts.append(
            f'<source document="{neutralise(chunk.document_title)}"{date_attr} tags="{tags}">\n'
            f"{FORGED_TAG.sub(lambda m: chr(0x2039) + m.group(1), chunk.content)}\n</source>"
        )
    return "\n\n".join(parts) if parts else "(no sources)"


def build_messages(
    company: str,
    question: str,
    chunks: Sequence[RetrievedChunk],
    history: Sequence[ChatMessage] = (),
    strict: bool = False,
    language: str | None = None,
    guard: str = "",
) -> list[ChatMessage]:
    system = prompts.ANSWER_SYSTEM.format(
        company=company, language_rule=prompts.language_rule(language), guard=guard
    )
    if strict:
        system += "\n" + prompts.STRICT_RULE
    user = prompts.ANSWER_USER.format(sources=format_sources(chunks), question=question)
    recent = list(history)[-MAX_HISTORY_MESSAGES:]
    return [
        {"role": "system", "content": system},
        *recent,
        {"role": "user", "content": user},
    ]


def stream_answer(
    llm: LLM,
    company: str,
    question: str,
    chunks: Sequence[RetrievedChunk],
    history: Sequence[ChatMessage] = (),
    model: str | None = None,
    strict: bool = False,
    language: str | None = None,
    guard: str = "",
) -> AsyncIterator[str]:
    messages = build_messages(company, question, chunks, history, strict, language, guard)
    return llm.stream(
        messages, model=model or get_settings().llm_model, max_tokens=MAX_ANSWER_TOKENS
    )


async def generate_answer(
    llm: LLM,
    company: str,
    question: str,
    chunks: Sequence[RetrievedChunk],
    history: Sequence[ChatMessage] = (),
    model: str | None = None,
) -> str:
    pieces = [t async for t in stream_answer(llm, company, question, chunks, history, model)]
    return "".join(pieces).strip()


def extract_citations(answer: str, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
    """Resolve citation tags in the answer to retrieved chunks; unknown tags are ignored."""
    by_cite: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        for tag in chunk.cites:
            by_cite.setdefault(tag, []).append(chunk)

    citations: list[Citation] = []
    seen: set[str] = set()
    for match in TAG_RE.finditer(answer):
        tag = match.group(1).strip()
        if tag in seen or tag not in by_cite:
            continue
        seen.add(tag)
        group = by_cite[tag]
        citations.append(
            Citation(
                cite=tag,
                document_id=group[0].document_id,
                document_title=group[0].document_title,
                section=group[0].section,
                page=group[0].page,
                chunk_ids=tuple(c.chunk_id for c in group),
            )
        )
    return citations


def strip_unknown_tags(answer: str, chunks: Sequence[RetrievedChunk]) -> str:
    valid = {tag for c in chunks for tag in c.cites}
    return TAG_RE.sub(lambda m: m.group(0) if m.group(1).strip() in valid else "", answer)


async def safe_fallback(llm: LLM, question: str) -> str:
    result = await llm.chat(
        [
            {"role": "system", "content": prompts.FALLBACK_SYSTEM},
            {"role": "user", "content": question},
        ],
        model=get_settings().llm_model_small,
        max_tokens=120,
    )
    return result.text.strip()
