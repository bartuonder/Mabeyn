import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from mabeyn.db.models import Conversation, RiskLevel, ToolCall, ToolCallStatus
from mabeyn.db.session import tenant_session
from mabeyn.guard.settings import load_guard_settings
from mabeyn.tools.executor import ToolContext
from mabeyn.tools.service import LoadedTool, ToolRunner, load_tools

ORDER_LOOKUP_TOOL = "get_order_status"
# Argument policies per tool. A tenant without the lookup tool cannot be checked and is not blocked.
ARGUMENT_POLICIES: dict[str, tuple[str, ...]] = {
    "request_refund": ("own_order", "refund_amount"),
    "change_address": ("own_order",),
}


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
        for policy in ARGUMENT_POLICIES.get(tool.name, ()):
            if violation := await self._argument_policy(policy, context, arguments):
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

    async def _order(self, context: ToolContext, order_id: str) -> tuple[str, dict[str, Any]]:
        lookup = {t.name: t for t in await load_tools(context.tenant_id)}.get(ORDER_LOOKUP_TOOL)
        if lookup is None:
            return "unchecked", {}
        result = await self.runner.executor.execute(
            lookup.endpoint, {"order_id": order_id}, context
        )
        if result.ok and isinstance(result.data, dict):
            return "found", result.data
        return ("missing" if result.status_code == 404 else "unavailable"), {}

    async def _argument_policy(
        self, policy: str, context: ToolContext, arguments: dict[str, Any]
    ) -> PolicyViolation | None:
        order_id = str(arguments.get("order_id", ""))
        state, order = await self._order(context, order_id)
        if state == "unchecked":
            return None
        if state == "missing":
            return PolicyViolation(policy, "that order does not belong to this customer")
        if state == "unavailable":
            return PolicyViolation(policy, "the order could not be verified")
        if policy == "refund_amount":
            total = order.get("total")
            amount = arguments.get("amount")
            if isinstance(total, int | float) and isinstance(amount, int | float):
                if amount > total + 0.005:
                    return PolicyViolation(policy, "the refund is larger than the order total")
        return None
