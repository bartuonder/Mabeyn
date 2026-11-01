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
    def cite(self) -> str:
        """Citation tag text: `document:section` using the first section's last heading."""
        first_path = self.section.split(SECTION_SEPARATOR)[0]
        heading = first_path.split(PATH_SEPARATOR)[-1]
        return f"{_clean(self.document_title)}:{_clean(heading)}"


@dataclass(frozen=True)
class Citation:
    cite: str
    document_id: uuid.UUID
    document_title: str
    section: str
    page: int | None
    chunk_ids: tuple[uuid.UUID, ...]
