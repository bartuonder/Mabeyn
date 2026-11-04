from fastapi import APIRouter, Response, status

from apps.api.deps import Admin
from mabeyn.cache.answers import invalidate_answers

router = APIRouter(prefix="/cache", tags=["cache"])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cache(admin: Admin) -> Response:
    """Drop every cached answer of the admin's tenant."""
    await invalidate_answers(admin.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
