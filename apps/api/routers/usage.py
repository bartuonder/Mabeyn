from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Date, case, cast, func, select

from apps.api.deps import TenantDb
from apps.api.schemas import UsageDay, UsageReport
from mabeyn.db.models import Message, MessageRole

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
async def daily_usage(db: TenantDb, days: Annotated[int, Query(ge=1, le=90)] = 7) -> UsageReport:
    """Tokens, estimated cost and latency of the assistant's replies, one row per day (UTC)."""
    since = datetime.now(UTC) - timedelta(days=days)
    day = cast(func.timezone("UTC", Message.created_at), Date)
    rows = await db.execute(
        select(
            day,
            func.count(Message.id),
            func.coalesce(func.sum(Message.input_tokens), 0),
            func.coalesce(func.sum(Message.output_tokens), 0),
            func.coalesce(func.sum(Message.llm_calls), 0),
            func.coalesce(func.sum(Message.cost_usd), 0),
            func.coalesce(func.sum(case((Message.cached, 1), else_=0)), 0),
            func.avg(Message.latency_ms),
        )
        .where(Message.role == MessageRole.ASSISTANT, Message.created_at >= since)
        .group_by(day)
        .order_by(day.desc())
    )
    report = [
        UsageDay(
            day=row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0])),
            messages=row[1],
            input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            llm_calls=int(row[4]),
            cost_usd=float(row[5]),
            cached_messages=int(row[6]),
            average_latency_ms=float(row[7]) if row[7] is not None else None,
        )
        for row in rows
    ]
    return UsageReport(
        days=report,
        total_cost_usd=round(sum(d.cost_usd for d in report), 6),
        total_messages=sum(d.messages for d in report),
    )
