from __future__ import annotations

import hmac
import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings

OPERATOR_KEY_HEADER = "X-Partizan-Operator-Key"
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# These are the only intentionally public unsafe-method endpoints. They are
# product-scoped data-plane calls and enforce their own Product Event Key.
PUBLIC_MUTATION_ROUTE_TEMPLATES = frozenset(
    {
        ("POST", "/v1/products/{product_id}/distribution-events"),
        ("POST", "/v1/products/{product_id}/distribution-events/verify"),
    }
)
_PUBLIC_MUTATION_PATHS = (
    re.compile(r"^/v1/products/[^/]+/distribution-events$"),
    re.compile(r"^/v1/products/[^/]+/distribution-events/verify$"),
)


def operator_auth_required(settings: Settings) -> bool:
    return settings.operator_auth_required or settings.app_env.strip().lower() in {
        "prod",
        "production",
    }


def _is_public_data_plane_mutation(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False
    return any(pattern.fullmatch(path) for pattern in _PUBLIC_MUTATION_PATHS)


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
    """Fail closed for every control-plane mutation when operator auth is active.

    The default is intentionally deny-by-default for unsafe HTTP methods. The
    only exemptions are the two product Event Key data-plane endpoints above.
    This protects legacy and future mutation routes even if an individual route
    forgets to attach ``Depends(require_operator)``.
    """

    method = request.method.upper()
    if method in SAFE_HTTP_METHODS:
        return
    if _is_public_data_plane_mutation(method, request.url.path):
        return
    _enforce_operator_key(settings, operator_key)


async def require_operator(
    settings: Annotated[Settings, Depends(get_settings)],
    operator_key: Annotated[str | None, Header(alias=OPERATOR_KEY_HEADER)] = None,
) -> None:
    _enforce_operator_key(settings, operator_key)
