from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.tracking_routes import router as tracking_router

_WEB_DIR = Path(__file__).resolve().parent / "web"
_OPERATOR_AUTH_SCRIPT = '<script src="/app/assets/operator-auth.v1.js" defer></script>'
_ASSETS = {
    "partizan.v1.css": "text/css; charset=utf-8",
    "partizan.v1.js": "text/javascript; charset=utf-8",
    "operator-auth.v1.css": "text/css; charset=utf-8",
    "operator-auth.v1.js": "text/javascript; charset=utf-8",
    "execution.v1.css": "text/css; charset=utf-8",
    "execution.v1.js": "text/javascript; charset=utf-8",
    "execution.v2.js": "text/javascript; charset=utf-8",
    "paid-control.v1.css": "text/css; charset=utf-8",
    "paid-control.v1.js": "text/javascript; charset=utf-8",
    "results.v1.css": "text/css; charset=utf-8",
    "results.v1.js": "text/javascript; charset=utf-8",
    "integration.v1.css": "text/css; charset=utf-8",
    "integration.v1.js": "text/javascript; charset=utf-8",
    "integration-status.v1.css": "text/css; charset=utf-8",
    "integration-status.v1.js": "text/javascript; charset=utf-8",
    "integration-guide.v1.css": "text/css; charset=utf-8",
    "integration-guide.v1.js": "text/javascript; charset=utf-8",
    "autonomy.v1.css": "text/css; charset=utf-8",
    "autonomy.v1.js": "text/javascript; charset=utf-8",
    "creative.v1.css": "text/css; charset=utf-8",
    "creative.v1.js": "text/javascript; charset=utf-8",
    "publishing.v1.css": "text/css; charset=utf-8",
    "publishing.v1.js": "text/javascript; charset=utf-8",
    "outreach.v1.css": "text/css; charset=utf-8",
    "outreach.v1.js": "text/javascript; charset=utf-8",
    "outreach-autosend.v1.css": "text/css; charset=utf-8",
    "outreach-autosend.v1.js": "text/javascript; charset=utf-8",
}
_LANDING_ASSETS = {
    "landing.v1.css": "text/css; charset=utf-8",
    "landing.v1.js": "text/javascript; charset=utf-8",
}
_START_ASSETS = {
    "start.v1.css": "text/css; charset=utf-8",
    "start.autopilot.v1.css": "text/css; charset=utf-8",
    "start.v2.css": "text/css; charset=utf-8",
    "start.v2.js": "text/javascript; charset=utf-8",
    "goal-dropdown.v1.css": "text/css; charset=utf-8",
    "goal-dropdown.v1.js": "text/javascript; charset=utf-8",
}

router = APIRouter(tags=["web"])
router.include_router(tracking_router)


@router.get("/", include_in_schema=False)
async def marketing_site() -> HTMLResponse:
    html = (_WEB_DIR / "landing.v1.html").read_text(encoding="utf-8")
    return HTMLResponse(html, media_type="text/html; charset=utf-8")


@router.get("/site/assets/{asset_name}", include_in_schema=False)
async def marketing_asset(asset_name: str) -> FileResponse:
    media_type = _LANDING_ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Marketing asset not found")
    return FileResponse(_WEB_DIR / asset_name, media_type=media_type)


@router.get("/start", include_in_schema=False)
async def customer_start() -> HTMLResponse:
    html = (_WEB_DIR / "start.v2.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/start/assets/{asset_name}", include_in_schema=False)
async def customer_start_asset(asset_name: str) -> FileResponse:
    media_type = _START_ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Customer onboarding asset not found")
    return FileResponse(_WEB_DIR / asset_name, media_type=media_type)


@router.get("/app", include_in_schema=False)
async def workspace() -> HTMLResponse:
    html = (_WEB_DIR / "index.v2.html").read_text(encoding="utf-8")
    marker = '<script src="/app/assets/partizan.v1.js" defer></script>'
    if marker not in html:
        raise HTTPException(status_code=500, detail="Workspace bootstrap marker missing")
    html = html.replace(marker, f"{_OPERATOR_AUTH_SCRIPT}\n  {marker}", 1)
    return HTMLResponse(html, media_type="text/html; charset=utf-8")


@router.get("/app/assets/{asset_name}", include_in_schema=False)
async def workspace_asset(asset_name: str) -> FileResponse:
    media_type = _ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Workspace asset not found")
    return FileResponse(_WEB_DIR / asset_name, media_type=media_type)
