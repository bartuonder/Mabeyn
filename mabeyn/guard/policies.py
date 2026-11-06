import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from mabeyn.db.models import Conversation, RiskLevel, ToolCall, ToolCallStatus
from mabeyn.db.session import tenant_session
from mabeyn.guard.policy_specs import (
    MaxAmount,
    PolicySpec,
    RequiredStatus,
    TimeWindow,
    parse_policies,
)
from mabeyn.guard.settings import load_guard_settings
from mabeyn.tools.executor import ToolContext
from mabeyn.tools.service import LoadedTool, ToolRunner, load_tools


@dataclass(frozen=True)
class PolicyViolation:
    policy: str
    reason: str


class ToolGuard:
    """Checks tool calls against the tenant's allowlist, argument policies and rate limit."""

    def __init__(self, runner: ToolRunner) -> None:
        self.runner = runner

    async def allowed_names(self, tenant_id: uuid.UUID) -> tuple[str, ...] | None:
        return (await load_guard_settings(tenant_id)).tool_allowlist

    async def check(
        self,
        context: ToolContext,
        conversation_id: uuid.UUID,
        tool: LoadedTool,
        arguments: dict[str, Any],
        *,
        include_rate: bool = True,
        today: date | None = None,
    ) -> PolicyViolation | None:
        settings = await load_guard_settings(context.tenant_id)
        if not settings.firewall_enabled:
            return None
        if settings.tool_allowlist is not None and tool.name not in settings.tool_allowlist:
            return PolicyViolation("allowlist", f"{tool.name} is not enabled for the assistant")
        if include_rate and tool.risk is not RiskLevel.READ:
            recent = await self._recent_changes(context, settings.write_rate_window_minutes)
            if recent >= settings.write_rate_limit:
                return PolicyViolation(
                    "rate_limit", "too many changes were requested recently; try again later"
                )
        records: dict[tuple[str, Any], tuple[str, dict[str, Any]]] = {}
        for spec in parse_policies(tool.policies):
            if violation := await self._argument_policy(spec, context, arguments, today, records):
                return violation
        return None

    async def _recent_changes(self, context: ToolContext, window_minutes: int) -> int:
        tools = await load_tools(context.tenant_id)
        writers = [t.name for t in tools if t.risk is not RiskLevel.READ]
        since = datetime.now(UTC) - timedelta(minutes=window_minutes)
        async with tenant_session(context.tenant_id) as session:
            count = await session.scalar(
                select(func.count(ToolCall.id))
                .join(Conversation, ToolCall.conversation_id == Conversation.id)
                .where(
                    Conversation.customer_id == context.customer_id,
                    ToolCall.tool_name.in_(writers),
                    ToolCall.status != ToolCallStatus.REJECTED,
                    ToolCall.created_at > since,
                )
            )
        return int(count or 0)

    async def _record(
        self,
        spec: PolicySpec,
        context: ToolContext,
        arguments: dict[str, Any],
        records: dict[tuple[str, Any], tuple[str, dict[str, Any]]],
    ) -> tuple[str, dict[str, Any]]:
        value = arguments.get(spec.id_argument)
        if (spec.lookup_tool, value) in records:
            return records[(spec.lookup_tool, value)]
        lookup = {t.name: t for t in await load_tools(context.tenant_id)}.get(spec.lookup_tool)
        if lookup is None:
            return "unavailable", {}
        result = await self.runner.executor.execute(
            lookup.endpoint, {spec.lookup_key: value}, context
        )
        if result.ok and isinstance(result.data, dict):
            found = ("found", result.data)
        else:
            found = ("missing" if result.status_code == 404 else "unavailable", {})
        records[(spec.lookup_tool, value)] = found
        return found

    async def _argument_policy(
        self,
        spec: PolicySpec,
        context: ToolContext,
        arguments: dict[str, Any],
        today: date | None,
        records: dict[tuple[str, Any], tuple[str, dict[str, Any]]],
    ) -> PolicyViolation | None:
        state, record = await self._record(spec, context, arguments, records)
        name = spec.type
        if state == "missing":
            return PolicyViolation(
                name, spec.message or "that record does not belong to this customer"
            )
        if state == "unavailable":
            return PolicyViolation(name, "the record could not be verified")
        if isinstance(spec, RequiredStatus):
            if record.get(spec.status_field) != spec.required_status:
                return PolicyViolation(
                    name, spec.message or f"only records with status {spec.required_status}"
                )
        if isinstance(spec, TimeWindow):
            start = record.get(spec.date_field)
            if start and (today or date.today()) - date.fromisoformat(str(start)) > timedelta(
                days=spec.days
            ):
                return PolicyViolation(name, spec.message or f"the {spec.days} day window passed")
        if isinstance(spec, MaxAmount):
            limit = record.get(spec.limit_field)
            amount = arguments.get(spec.amount_argument)
            if isinstance(limit, int | float) and isinstance(amount, int | float):
                if amount > limit + 0.005:
                    return PolicyViolation(
                        name, spec.message or "the amount is larger than the record allows"
                    )
        return None
