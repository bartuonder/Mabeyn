from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # The read tool that returns the record the call refers to, and the argument naming it.
    lookup_tool: str = Field(min_length=3, max_length=64)
    id_argument: str = Field(min_length=1, max_length=64)
    # Argument name the lookup tool expects, when it differs from `id_argument`.
    lookup_argument: str | None = None
    # What the assistant tells the customer when the policy blocks the call.
    message: str | None = Field(default=None, max_length=200)

    @property
    def lookup_key(self) -> str:
        return self.lookup_argument or self.id_argument


class OwnedRecord(_Spec):
    """The record must exist for this customer (the lookup runs with the customer's identity)."""

    type: Literal["owned_record"]


class MaxAmount(_Spec):
    """A money argument may not exceed a number field of the record."""

    type: Literal["max_amount"]
    amount_argument: str = Field(min_length=1, max_length=64)
    limit_field: str = Field(min_length=1, max_length=64)


class RequiredStatus(_Spec):
    """A field of the record must have one value, for example status equal to delivered."""

    type: Literal["required_status"]
    status_field: str = Field(min_length=1, max_length=64)
    required_status: str = Field(min_length=1, max_length=64)


class TimeWindow(_Spec):
    """The record must be younger than `days`, counted from a date field."""

    type: Literal["time_window"]
    date_field: str = Field(min_length=1, max_length=64)
    days: int = Field(ge=1, le=3650)


PolicySpec = Annotated[
    OwnedRecord | MaxAmount | RequiredStatus | TimeWindow, Field(discriminator="type")
]
_ADAPTER: TypeAdapter[list[PolicySpec]] = TypeAdapter(list[PolicySpec])


def parse_policies(raw: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[PolicySpec]:
    return _ADAPTER.validate_python(list(raw))


def dump_policies(specs: list[PolicySpec]) -> list[dict[str, Any]]:
    return [spec.model_dump(exclude_none=True) for spec in specs]
