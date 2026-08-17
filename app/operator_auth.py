from __future__ import annotations

import hmac
import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings

OPERATOR_KEY_HEADER = "X-Partizan-Operator-Key"

# These are the only intentionally public /v1 endpoints. Conversion ingestion
# enforces its own Product Event Key. Customer routes enforce an opaque per-project
# token, except preview creation, OAuth callback, Stripe-verified recovery and the
# Stripe-signed webhook.
PUBLIC_API_ROUTE_TEMPLATES = frozenset(
    {
        ("POST", "/v1/products/{product_id}/distribution-events"),
        ("POST", "/v1/products/{product_id}/distribution-events/verify"),
        ("GET", "/v1/public/creative-blobs/{blob_id}"),
        ("POST", "/v1/customer-projects/preview"),
        ("GET", "/v1/customer-projects/{project_id}"),
        ("POST", "/v1/customer-projects/{project_id}/checkout"),
        ("POST", "/v1/customer-projects/{project_id}/recover-access"),
        ("POST", "/v1/customer-projects/{project_id}/deep-research"),
        ("POST", "/v1/customer-projects/{project_id}/clarifications"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/checkout"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/verify"),
        ("PUT", "/v1/customer-projects/{project_id}/autopilot"),
        ("GET", "/v1/customer-projects/{project_id}/autopilot"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/status"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/meta/connect"),
        ("GET", "/v1/customer-projects/{project_id}/autopilot/meta/options"),
        ("POST", "/v1/customer-projects/{project_id}/autopilot/meta/connection"),
        ("GET", "/v1/customer-meta/oauth/callback"),
        ("POST", "/v1/billing/stripe/webhook"),
    }
)
_PUBLIC_API_PATHS = (
    ("POST", re.compile(r"^/v1/products/[^/]+/distribution-events$")),
    ("POST", re.compile(r"^/v1/products/[^/]+/distribution-events/verify$")),
    ("GET", re.compile(r"^/v1/public/creative-blobs/[^/]+$")),
    ("POST", re.compile(r"^/v1/customer-projects/preview$")),
    ("GET", re.compile(r"^/v1/customer-projects/[^/]+$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/checkout$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/recover-access$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/deep-research$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/clarifications$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/checkout$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/verify$")),
    ("PUT", re.compile(r"^/v1/customer-projects/[^/]+/autopilot$")),
    ("GET", re.compile(r"^/v1/customer-projects/[^/]+/autopilot$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/status$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/meta/connect$")),
    ("GET", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/meta/options$")),
    ("POST", re.compile(r"^/v1/customer-projects/[^/]+/autopilot/meta/connection$")),
    ("GET", re.compile(r"^/v1/customer-meta/oauth/callback$")),
    ("POST", re.compile(r"^/v1/billing/stripe/webhook$")),
)


def operator_auth_required(settings: Settings) -> bool:
    return settings.operator_auth_required or settings.app_env.strip().lower() in {
        "prod",
        "production",
    }


def _is_public_api_route(method: str, path: str) -> bool:
    normalized_method = method.upper()
    return any(
        route_method == normalized_method and pattern.fullmatch(path)
        for route_method, pattern in _PUBLIC_API_PATHS
    )


def _enforce_operator_key(settings: Settings, operator_key: str | None) -> None:
    if not operator_auth_required(settings):
        return
    configured = settings.operator_api_key
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operator authentication is required but not configured",
        )
    expected = configured.get_secret_value()
    if operator_key is None or not hmac.compare_digest(operator_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operator authentication required",
            headers={"WWW-Authenticate": "PartizanOperatorKey"},
        )


async def require_control_plane_operator(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    operator_key: Annotated[str | None, Header(alias=OPERATOR_KEY_HEADER)] = None,
) -> None:
    """Protect every internal ``/v1`` control-plane request when auth is active.

    Health, browser assets and tracking redirects live outside ``/v1`` and stay
    public. Inside ``/v1`` the default is fail-closed for both reads and writes;
    only explicit data-plane, customer-boundary and public-asset routes bypass
    operator auth. Each customer-boundary route applies its own authentication.
    """

    method = request.method.upper()
    path = request.url.path
    if not path.startswith("/v1/"):
        return
    if method == "OPTIONS":
        return
    if _is_public_api_route(method, path):
        return
    _enforce_operator_key(settings, operator_key)


async def require_operator(
    settings: Annotated[Settings, Depends(get_settings)],
    operator_key: Annotated[str | None, Header(alias=OPERATOR_KEY_HEADER)] = None,
) -> None:
    _enforce_operator_key(settings, operator_key)
