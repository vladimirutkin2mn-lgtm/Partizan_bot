from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

OPERATOR_KEY_HEADER = "X-Partizan-Operator-Key"


def operator_auth_required(settings: Settings) -> bool:
    return settings.operator_auth_required or settings.app_env.strip().lower() in {
        "prod",
        "production",
    }


async def require_operator(
    settings: Annotated[Settings, Depends(get_settings)],
    operator_key: Annotated[str | None, Header(alias=OPERATOR_KEY_HEADER)] = None,
) -> None:
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
