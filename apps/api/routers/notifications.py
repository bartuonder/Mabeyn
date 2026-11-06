from fastapi import APIRouter, HTTPException, status

from apps.api.deps import Admin
from apps.api.schemas import NotificationSettingsOut, NotificationSettingsUpdate
from mabeyn.notify.webhook import WebhookError, load_webhook_url, save_webhook

router = APIRouter(tags=["notifications"])


@router.get("/settings/notifications")
async def get_notification_settings(admin: Admin) -> NotificationSettingsOut:
    return NotificationSettingsOut(approval_webhook_url=await load_webhook_url(admin.tenant_id))


@router.put("/settings/notifications")
async def update_notification_settings(
    body: NotificationSettingsUpdate, admin: Admin
) -> NotificationSettingsOut:
    """Set the URL that hears about new pending approvals; the signing secret is shown once."""
    try:
        secret = await save_webhook(admin.tenant_id, body.approval_webhook_url)
    except WebhookError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return NotificationSettingsOut(
        approval_webhook_url=body.approval_webhook_url, signing_secret=secret
    )
