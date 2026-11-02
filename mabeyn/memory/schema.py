import enum
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
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
        value = value.lower().replace("-", "_").replace(" ", "_")
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
