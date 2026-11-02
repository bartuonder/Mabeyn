import enum
import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
NOT_SLUG = re.compile(r"[^a-z0-9]+")
MAX_SLUG_CHARS = 40
MAX_TEXT_CHARS = 200


class MemoryCategory(enum.StrEnum):
    PREFERENCE = "preference"
    ISSUE = "issue"
    PRODUCT = "product"
    CONTEXT = "context"


class MemoryRecord(BaseModel):
    """One durable fact the customer stated about themselves. Nothing else is ever stored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: MemoryCategory
    slug: str
    text: str = Field(min_length=3, max_length=MAX_TEXT_CHARS)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        ascii_text = unicodedata.normalize("NFKD", value.replace("ı", "i")).encode(
            "ascii", "ignore"
        )
        value = (
            NOT_SLUG.sub("_", ascii_text.decode().lower()).strip("_")[:MAX_SLUG_CHARS].strip("_")
        )
        if not SLUG.match(value):
            raise ValueError("slug must be a short snake_case name")
        return value

    @property
    def key(self) -> str:
        return f"{self.category.value}:{self.slug}"


class StoredMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    category: MemoryCategory
    text: str
    conversation_id: str | None = None
    updated_at: datetime | None = None
