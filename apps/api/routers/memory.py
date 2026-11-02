from typing import Annotated

from fastapi import APIRouter, Depends

from apps.api.deps import Admin, Customer
from apps.api.schemas import MemoryDeleted, MemoryOut
from mabeyn.agents.runtime import get_memory_service
from mabeyn.memory.service import MemoryService

router = APIRouter(tags=["memory"])

Memories = Annotated[MemoryService, Depends(get_memory_service)]


@router.get("/me/memory")
async def my_memory(customer: Customer, memory: Memories) -> list[MemoryOut]:
    notes = await memory.list(customer.tenant_id, customer.customer_id)
    return [MemoryOut.model_validate(n) for n in notes]


@router.delete("/me/memory")
async def forget_me(customer: Customer, memory: Memories) -> MemoryDeleted:
    return MemoryDeleted(deleted=await memory.forget(customer.tenant_id, customer.customer_id))


@router.get("/customers/{customer_id}/memory")
async def customer_memory(customer_id: str, admin: Admin, memory: Memories) -> list[MemoryOut]:
    notes = await memory.list(admin.tenant_id, customer_id)
    return [MemoryOut.model_validate(n) for n in notes]


@router.delete("/customers/{customer_id}/memory")
async def forget_customer(customer_id: str, admin: Admin, memory: Memories) -> MemoryDeleted:
    """Erase everything remembered about one customer of the admin's tenant (KVKK, GDPR)."""
    return MemoryDeleted(deleted=await memory.forget(admin.tenant_id, customer_id))
