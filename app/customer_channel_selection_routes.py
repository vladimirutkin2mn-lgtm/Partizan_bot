from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_channel_schemas import (
    CustomerChannelSelectionRequest,
    CustomerChannelView,
)
from app.customer_channels import customer_channel_service
from app.customer_funnel import CustomerProjectAccessError, CustomerProjectNotFoundError

router = APIRouter(tags=["customer-channel-selection"])


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _project_token(session_token: str | None, project_id: UUID) -> str:
    try:
        _, customer_token = customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
        return customer_token
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(status_code=403, detail="This project does not belong to this account") from exc


@router.put(
    "/customer/workspace/{project_id}/channel-selection",
    response_model=list[CustomerChannelView],
)
def select_customer_acquisition_channel(
    project_id: UUID,
    payload: CustomerChannelSelectionRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[CustomerChannelView]:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_channel_service.select(
            project_id,
            customer_token,
            payload.platform,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
