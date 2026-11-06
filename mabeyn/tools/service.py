import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from mabeyn.db.models import RiskLevel, ToolCall, ToolCallStatus, ToolDefinition
from mabeyn.db.session import tenant_session
from mabeyn.tools.executor import (
    PLACEHOLDER,
    HttpToolExecutor,
    ToolContext,
    ToolEndpoint,
    ToolResult,
)
from mabeyn.tools.schema import (
    RESERVED_ARGUMENTS,
    ToolSchemaError,
    build_args_model,
    openai_tool_spec,
)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
STORED_RESULT_CHARS = 2000


@dataclass(frozen=True)
class LoadedTool:
    id: uuid.UUID
    name: str
    description: str
    schema: dict[str, Any]
    risk: RiskLevel
    method: str
    url_template: str
    requires_identity: bool = True
    policies: tuple[dict[str, Any], ...] = ()

    @property
    def endpoint(self) -> ToolEndpoint:
        return ToolEndpoint(self.method, self.url_template)

    def spec(self) -> dict[str, Any]:
        return openai_tool_spec(self.name, self.description, self.schema)

    def args_model(self) -> type[BaseModel]:
        return build_args_model(self.name, self.schema)


def validate_definition(name: str, schema: dict[str, Any], method: str, url_template: str) -> None:
    """Reject tool definitions that cannot be executed safely."""
    if not NAME_RE.match(name):
        raise ToolSchemaError("name must be snake_case, 3-64 characters")
    if method.upper() not in HTTP_METHODS:
        raise ToolSchemaError(f"method must be one of {sorted(HTTP_METHODS)}")
    parsed = urlparse(url_template.replace("{", "x").replace("}", "x"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolSchemaError("url_template must be an absolute http(s) URL")
    build_args_model(name, schema)
    required = set(schema.get("required", []))
    for placeholder in PLACEHOLDER.findall(url_template):
        if placeholder in RESERVED_ARGUMENTS:
            raise ToolSchemaError(f"{placeholder} cannot be a URL parameter")
        if placeholder not in schema["properties"] or placeholder not in required:
            raise ToolSchemaError(f"URL parameter {placeholder} must be a required property")


def validate_policies(
    schema: dict[str, Any], policies: list[Any], existing: dict[str, Any]
) -> None:
    """A policy must name a read tool of the tenant and arguments the tool really has."""
    properties = schema.get("properties", {})
    for spec in policies:
        lookup = existing.get(spec.lookup_tool)
        if lookup is None or lookup.risk_level is not RiskLevel.READ:
            raise ToolSchemaError(f"policy lookup_tool {spec.lookup_tool} must be a read tool")
        if spec.id_argument not in properties:
            raise ToolSchemaError(f"policy id_argument {spec.id_argument} is not a property")
        amount = getattr(spec, "amount_argument", None)
        if amount is not None and amount not in properties:
            raise ToolSchemaError(f"policy amount_argument {amount} is not a property")


def _loaded(row: ToolDefinition) -> LoadedTool:
    return LoadedTool(
        row.id, row.name, row.description, row.json_schema, row.risk_level, row.http_method,
        row.url_template, row.requires_identity, tuple(row.policies or ()),
    )  # fmt: skip


async def load_tools(
    tenant_id: uuid.UUID, risks: frozenset[RiskLevel] | None = None
) -> list[LoadedTool]:
    async with tenant_session(tenant_id) as session:
        rows = await session.scalars(select(ToolDefinition).order_by(ToolDefinition.name))
        tools = [_loaded(r) for r in rows]
    return [t for t in tools if risks is None or t.risk in risks]


async def start_call(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    tool_name: str,
    arguments: dict[str, Any],
    status: ToolCallStatus = ToolCallStatus.PENDING,
) -> uuid.UUID:
    async with tenant_session(tenant_id) as session:
        call = ToolCall(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
        )
        session.add(call)
        await session.flush()
        return call.id


async def finish_call(tenant_id: uuid.UUID, call_id: uuid.UUID, result: ToolResult) -> None:
    async with tenant_session(tenant_id) as session:
        call = await session.get(ToolCall, call_id)
        if call is None:
            return
        call.status = ToolCallStatus.SUCCESS if result.ok else ToolCallStatus.ERROR
        call.result = {"status": result.status_code, "data": _storable(result.data)}
        call.error = result.error
        call.duration_ms = result.duration_ms


async def reject_call(tenant_id: uuid.UUID, call_id: uuid.UUID, reason: str) -> None:
    async with tenant_session(tenant_id) as session:
        call = await session.get(ToolCall, call_id)
        if call is not None:
            call.status = ToolCallStatus.REJECTED
            call.error = reason[:500]


def _storable(data: Any) -> Any:
    """Keep small results as structured JSON and cut large ones."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    return data if len(text) <= STORED_RESULT_CHARS else {"truncated": text[:STORED_RESULT_CHARS]}


async def finished_result(tenant_id: uuid.UUID, call_id: uuid.UUID) -> ToolResult | None:
    """The stored outcome of a call that already ran, so it is never executed twice."""
    async with tenant_session(tenant_id) as session:
        call = await session.get(ToolCall, call_id)
        if call is None or call.status not in (ToolCallStatus.SUCCESS, ToolCallStatus.ERROR):
            return None
        stored = call.result or {}
        return ToolResult(
            ok=call.status is ToolCallStatus.SUCCESS,
            data=stored.get("data"),
            error=call.error,
            status_code=stored.get("status"),
            duration_ms=call.duration_ms or 0,
        )


def parse_arguments(tool: LoadedTool, raw: str) -> dict[str, Any] | str:
    """Validate model-produced arguments; returns the cleaned dict or an error message."""
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return "arguments are not valid JSON"
    if not isinstance(data, dict):
        return "arguments must be a JSON object"
    try:
        model = tool.args_model().model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return f"invalid arguments: {problems}"
    return model.model_dump(exclude_none=True)


class ToolRunner:
    """Validates, executes and audits one tool call on behalf of a verified customer."""

    def __init__(self, executor: HttpToolExecutor | None = None) -> None:
        self.executor = executor or HttpToolExecutor()

    async def run(
        self,
        context: ToolContext,
        conversation_id: uuid.UUID,
        tool: LoadedTool,
        arguments: dict[str, Any],
        call_id: uuid.UUID | None = None,
    ) -> ToolResult:
        if context.anonymous and tool.requires_identity:
            return ToolResult(
                ok=False, data=None, error="this tool needs a signed-in customer", status_code=403
            )
        call_id = call_id or await start_call(
            context.tenant_id, conversation_id, tool.name, arguments
        )
        if (done := await finished_result(context.tenant_id, call_id)) is not None:
            return done
        result = await self.executor.execute(
            tool.endpoint, arguments, context, idempotency_key=str(call_id)
        )
        await finish_call(context.tenant_id, call_id, result)
        return result
