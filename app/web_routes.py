import hashlib
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
    "landing.account.v1.css": "text/css; charset=utf-8",
    "landing.v1.js": "text/javascript; charset=utf-8",
}
_START_ASSETS = {
    "start.v1.css": "text/css; charset=utf-8",
    "start.autopilot.v1.css": "text/css; charset=utf-8",
    "start.v2.css": "text/css; charset=utf-8",
    "start.v2.js": "text/javascript; charset=utf-8",
    "start.channels.v1.js": "text/javascript; charset=utf-8",
    "goal-dropdown.v1.css": "text/css; charset=utf-8",
    "goal-dropdown.v1.js": "text/javascript; charset=utf-8",
    "customer-account.v1.css": "text/css; charset=utf-8",
}
_CUSTOMER_WORKSPACE_ASSETS = {
    "workspace.v1.css": "text/css; charset=utf-8",
    "workspace.v1.js": "text/javascript; charset=utf-8",
    "workspace.channels.v1.css": "text/css; charset=utf-8",
    "workspace.channels.v1.js": "text/javascript; charset=utf-8",
    "workspace.projects.v1.css": "text/css; charset=utf-8",
    "workspace.projects.v1.js": "text/javascript; charset=utf-8",
    "workspace.experiments.v1.css": "text/css; charset=utf-8",
    "workspace.experiments.v1.js": "text/javascript; charset=utf-8",
}
_WORKSPACE_STYLESHEET_MARKER = '<link rel="stylesheet" href="/workspace/assets/workspace.v1.css">'
_WORKSPACE_CHANNEL_STYLESHEET = (
    '<link rel="stylesheet" href="/workspace/assets/workspace.channels.v1.css">'
)
_WORKSPACE_PROJECT_STYLESHEET = (
    '<link rel="stylesheet" href="/workspace/assets/workspace.projects.v1.css">'
)
_WORKSPACE_EXPERIMENT_STYLESHEET = (
    '<link rel="stylesheet" href="/workspace/assets/workspace.experiments.v1.css">'
)
_WORKSPACE_SCRIPT_MARKER = '<script src="/workspace/assets/workspace.v1.js" defer></script>'
_WORKSPACE_CHANNEL_SCRIPT = (
    '<script src="/workspace/assets/workspace.channels.v1.js" defer></script>'
)
_WORKSPACE_PROJECT_SCRIPT = (
    '<script src="/workspace/assets/workspace.projects.v1.js" defer></script>'
)
_WORKSPACE_EXPERIMENT_SCRIPT = (
    '<script src="/workspace/assets/workspace.experiments.v1.js" defer></script>'
)


def _workspace_asset_revision() -> str:
    digest = hashlib.sha256()
    for asset_name in sorted(_CUSTOMER_WORKSPACE_ASSETS):
        digest.update(asset_name.encode("utf-8"))
        digest.update((_WEB_DIR / asset_name).read_bytes())
    return digest.hexdigest()[:12]


_CUSTOMER_WORKSPACE_ASSET_REVISION = _workspace_asset_revision()
_LANDING_STYLESHEET_MARKER = '<link rel="stylesheet" href="/site/assets/landing.v1.css">'
_LANDING_ACCOUNT_STYLESHEET = (
    '<link rel="stylesheet" href="/site/assets/landing.account.v1.css">'
)
_LANDING_START_CTA = (
    '<a class="button button-nav" href="/start">Open Partizan <span>↗</span></a>'
)
_LANDING_NAV_ACTIONS = (
    '<div class="nav-actions">'
    '<a id="nav-account-link" class="nav-account-link" href="/workspace">Sign in</a>'
    f"{_LANDING_START_CTA}"
    "</div>"
)

router = APIRouter(tags=["web"])
router.include_router(tracking_router)


@router.get("/", include_in_schema=False)
async def marketing_site() -> HTMLResponse:
    html = (_WEB_DIR / "landing.v1.html").read_text(encoding="utf-8")
    if _LANDING_STYLESHEET_MARKER not in html or _LANDING_START_CTA not in html:
        raise HTTPException(status_code=500, detail="Marketing navigation marker missing")
    html = html.replace(
        _LANDING_STYLESHEET_MARKER,
        f"{_LANDING_STYLESHEET_MARKER}\n  {_LANDING_ACCOUNT_STYLESHEET}",
        1,
    )
    html = html.replace(_LANDING_START_CTA, _LANDING_NAV_ACTIONS, 1)
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


@router.get("/workspace", include_in_schema=False)
async def customer_workspace() -> HTMLResponse:
    html = (_WEB_DIR / "workspace.v1.html").read_text(encoding="utf-8")
    if _WORKSPACE_STYLESHEET_MARKER not in html or _WORKSPACE_SCRIPT_MARKER not in html:
        raise HTTPException(status_code=500, detail="Customer workspace enhancement marker missing")
    html = html.replace(
        _WORKSPACE_STYLESHEET_MARKER,
        (
            f"{_WORKSPACE_STYLESHEET_MARKER}\n  {_WORKSPACE_CHANNEL_STYLESHEET}"
            f"\n  {_WORKSPACE_PROJECT_STYLESHEET}"
            f"\n  {_WORKSPACE_EXPERIMENT_STYLESHEET}"
        ),
        1,
    )
    html = html.replace(
        _WORKSPACE_SCRIPT_MARKER,
        (
            f"{_WORKSPACE_SCRIPT_MARKER}\n  {_WORKSPACE_CHANNEL_SCRIPT}"
            f"\n  {_WORKSPACE_PROJECT_SCRIPT}"
            f"\n  {_WORKSPACE_EXPERIMENT_SCRIPT}"
        ),
        1,
    )
    for asset_name in _CUSTOMER_WORKSPACE_ASSETS:
        asset_url = f"/workspace/assets/{asset_name}"
        versioned_url = f"{asset_url}?v={_CUSTOMER_WORKSPACE_ASSET_REVISION}"
        if asset_url not in html:
            raise HTTPException(status_code=500, detail="Customer workspace asset marker missing")
        html = html.replace(asset_url, versioned_url, 1)
    return HTMLResponse(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/workspace/assets/{asset_name}", include_in_schema=False)
async def customer_workspace_asset(asset_name: str) -> FileResponse:
    media_type = _CUSTOMER_WORKSPACE_ASSETS.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Customer workspace asset not found")
    return FileResponse(
        _WEB_DIR / asset_name,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


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
