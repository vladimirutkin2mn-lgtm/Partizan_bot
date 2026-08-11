from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.tracking_routes import router as tracking_router

_WEB_DIR = Path(__file__).resolve().parent / "web"
_ASSETS = {
    "partizan.v1.css": "text/css; charset=utf-8",
    "partizan.v1.js": "text/javascript; charset=utf-8",
    "execution.v1.css": "text/css; charset=utf-8",
    "execution.v1.js": "text/javascript; charset=utf-8",
    "execution.v2.js": "text/javascript; charset=utf-8",
    "paid-control.v1.css": "text/css; charset=utf-8",
    "paid-control.v1.js": "text/javascript; charset=utf-8",
    "results.v1.css": "text/css; charset=utf-8",
    "results.v1.js": "text/javascript; charset=utf-8",
    "integration.v1.css": "text/css; charset=utf-8",
    "integration.v1.js": "text/javascript; charset=utf-8",
}

router = APIRouter(tags=["web"])
router.include_router(tracking_router)


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/app")


@router.get("/app", include_in_schema=False)
async def workspace() -> FileResponse:
    return FileResponse(_WEB_DIR / "index.v2.html", media_type="text/html; charset=utf-8")


@router.get("/app/assets/{asset_name}", include_in_schema=False)
async def workspace_asset(asset_name: str) -> FileResponse:
    media_type = _ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Workspace asset not found")
    return FileResponse(_WEB_DIR / asset_name, media_type=media_type)
