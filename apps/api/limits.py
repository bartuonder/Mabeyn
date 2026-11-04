from typing import Annotated

from fastapi import Depends, HTTPException, status

from apps.api.deps import Customer
from mabeyn.core.config import get_settings
from mabeyn.core.ratelimit import RateLimiter, get_rate_limiter

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def limit_chat(customer: Customer, limiter: Limiter) -> None:
    """Sliding windows per customer, per API key and per tenant; the narrowest one fires first."""
    settings = get_settings()
    window = settings.chat_rate_window_seconds
    who = f"{customer.tenant_id}:{customer.customer_id}"
    windows = [(f"chat:customer:{who}", settings.chat_rate_per_customer)]
    if customer.key_id is not None:
        windows.append((f"chat:key:{customer.key_id}", settings.chat_rate_per_key))
    windows.append((f"chat:tenant:{customer.tenant_id}", settings.chat_rate_per_tenant))
    for key, limit in windows:
        if not await limiter.allow(key, limit, window):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many messages, slow down",
                headers={"Retry-After": str(window)},
            )


ChatLimit = Depends(limit_chat)
