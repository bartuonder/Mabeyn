from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["widget"])

WIDGET = Path(__file__).resolve().parents[1] / "static" / "widget.js"


@router.get("/widget.js", include_in_schema=False)
async def widget_script() -> FileResponse:
    """The embeddable chat widget: one script tag on the tenant's site."""
    return FileResponse(
        WIDGET,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )
