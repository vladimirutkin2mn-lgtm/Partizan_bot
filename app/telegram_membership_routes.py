"""Authenticated API for bounded Telegram linked-group membership operations."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
)
from app.operator_auth import require_operator
from app.telegram_membership_control import (
    TelegramMembershipApproveRequest,
    TelegramMembershipError,
    TelegramMembershipPreviewRequest,
    telegram_membership_service,
)

customer_router = APIRouter(tags=["customer-telegram-membership"])
operator_router = APIRouter(
    prefix="/v1", tags=["operator-telegram-membership"],
    dependencies=[Depends(require_operator)],
)


def _require_owner(session_token: str | None, project_id: UUID) -> None:
    try:
        customer_account_service.project_access(
            session_token=session_token, project_id=project_id,
        )
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Authentication required") from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(status_code=403, detail="Not the project owner") from exc


def _conflict(exc: TelegramMembershipError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@customer_router.post(
    "/customer/workspace/{project_id}/telegram/linked-group-joins/preview",
)
async def preview_customer_telegram_group_join(
    project_id: UUID,
    payload: TelegramMembershipPreviewRequest,
    session_token: Annotated[
        str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)
    ] = None,
) -> dict:
    _require_owner(session_token, project_id)
    try:
        return await telegram_membership_service.preview(project_id, payload.handle)
    except TelegramMembershipError as exc:
        raise _conflict(exc) from exc


@customer_router.post(
    "/customer/workspace/{project_id}/telegram/linked-group-joins/{intent_id}/approve",
)
def approve_customer_telegram_group_join(
    project_id: UUID,
    intent_id: UUID,
    payload: TelegramMembershipApproveRequest,
    session_token: Annotated[
        str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)
    ] = None,
) -> dict:
    _require_owner(session_token, project_id)
    try:
        return telegram_membership_service.approve(project_id, intent_id, payload)
    except TelegramMembershipError as exc:
        raise _conflict(exc) from exc


@customer_router.get(
    "/customer/workspace/{project_id}/telegram/linked-group-joins/{intent_id}",
)
def view_customer_telegram_group_join(
    project_id: UUID,
    intent_id: UUID,
    session_token: Annotated[
        str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)
    ] = None,
) -> dict:
    _require_owner(session_token, project_id)
    try:
        return telegram_membership_service.get(project_id, intent_id)
    except TelegramMembershipError as exc:
        raise _conflict(exc) from exc


@operator_router.post(
    "/telegram/membership/{project_id}/linked-group-joins/{intent_id}/execute",
)
async def execute_approved_telegram_group_join(
    project_id: UUID, intent_id: UUID,
) -> dict:
    """Operator/agent can execute ONLY an unexpired owner-approved intent."""
    try:
        return await telegram_membership_service.execute(project_id, intent_id)
    except TelegramMembershipError as exc:
        raise _conflict(exc) from exc


@operator_router.get(
    "/telegram/membership/{project_id}/linked-group-joins/{intent_id}",
)
def view_operator_telegram_group_join(
    project_id: UUID, intent_id: UUID,
) -> dict:
    try:
        return telegram_membership_service.get(project_id, intent_id)
    except TelegramMembershipError as exc:
        raise _conflict(exc) from exc
