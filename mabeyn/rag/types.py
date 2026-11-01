import re
import uuid
from dataclasses import dataclass
from datetime import date

from mabeyn.ingestion.chunk import PATH_SEPARATOR, SECTION_SEPARATOR


def _clean(text: str) -> str:
    return re.sub(r"[\[\]:]", " ", text).strip()


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_uri: str | None
    section: str
    page: int | None
    content: str
    context_header: str
    effective_date: date | None
    score: float

    @property
    def cites(self) -> list[str]:
        """One `document:heading` tag per section the chunk covers."""
        title = _clean(self.document_title)
        headings = [
            label.split(PATH_SEPARATOR)[-1] for label in self.section.split(SECTION_SEPARATOR)
        ]
        return [f"{title}:{_clean(h)}" for h in headings if h.strip()] or [title + ":"]

    @property
    def cite(self) -> str:
        return self.cites[0]


@dataclass(frozen=True)
class Citation:
    cite: str
    document_id: uuid.UUID
    document_title: str
    section: str
    page: int | None
    chunk_ids: tuple[uuid.UUID, ...]
