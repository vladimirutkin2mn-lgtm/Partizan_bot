from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_autopilot import customer_autopilot_service
from app.customer_channel_schemas import (
    CustomerChannelPreferencesUpdateRequest,
    CustomerChannelView,
)
from app.customer_channels import customer_channel_service
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.telegram_client_governance import (
    TelegramAutomationAuthorizationRequest,
    TelegramAutomationView,
    TelegramPublishObservationView,
    customer_telegram_governance_service,
)
from app.telegram_client_publishing import (
    CustomerTelegramClientPublishError,
    TelegramClientPublishReceipt,
    TelegramConnectionView,
    TelegramLoginChallengeView,
    TelegramLoginConfirmRequest,
    TelegramLoginStartRequest,
    TelegramPublishRequest,
    customer_telegram_client_publish_service,
)

router = APIRouter(tags=["customer-channels"])


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


@router.get(
    "/customer/workspace/{project_id}/channels",
    response_model=list[CustomerChannelView],
)
def get_customer_channel_controls(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[CustomerChannelView]:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_channel_service.list(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/customer/workspace/{project_id}/channels",
    response_model=list[CustomerChannelView],
)
def update_customer_channel_controls(
    project_id: UUID,
    payload: CustomerChannelPreferencesUpdateRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[CustomerChannelView]:
    customer_token = _project_token(session_token, project_id)
    try:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        manually_paused = project.get("autopilot_pause_reason") == "CUSTOMER"
        customer_channel_service.update(project_id, customer_token, payload)
        if not manually_paused:
            customer_autopilot_service.refresh_channel_policy(project_id, customer_token)
        return customer_channel_service.list(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/connection",
    response_model=TelegramConnectionView,
)
def get_customer_telegram_connection(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramConnectionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_client_publish_service.connection(project_id, customer_token)
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/connection/start",
    response_model=TelegramLoginChallengeView,
)
async def start_customer_telegram_connection(
    project_id: UUID,
    payload: TelegramLoginStartRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramLoginChallengeView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await customer_telegram_client_publish_service.begin_login(
            project_id,
            customer_token,
            payload,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Telegram connection failed safely ({type(exc).__name__})",
        ) from None


@router.post(
    "/customer/workspace/{project_id}/telegram/connection/confirm",
    response_model=TelegramConnectionView | TelegramLoginChallengeView,
)
async def confirm_customer_telegram_connection(
    project_id: UUID,
    payload: TelegramLoginConfirmRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramConnectionView | TelegramLoginChallengeView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await customer_telegram_client_publish_service.complete_login(
            project_id,
            customer_token,
            payload,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Telegram connection failed safely ({type(exc).__name__})",
        ) from None


@router.delete(
    "/customer/workspace/{project_id}/telegram/connection",
    response_model=TelegramConnectionView,
)
def disconnect_customer_telegram_connection(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramConnectionView:
    customer_token = _project_token(session_token, project_id)
    try:
        result = customer_telegram_client_publish_service.disconnect(project_id, customer_token)
        customer_telegram_governance_service.revoke_automation(project_id, customer_token)
        return result
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/automation",
    response_model=TelegramAutomationView,
)
def get_customer_telegram_automation(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramAutomationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_governance_service.automation_status(project_id, customer_token)
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/customer/workspace/{project_id}/telegram/automation",
    response_model=TelegramAutomationView,
)
def authorize_customer_telegram_automation(
    project_id: UUID,
    payload: TelegramAutomationAuthorizationRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramAutomationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_governance_service.authorize_automation(
            project_id,
            customer_token,
            payload,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/automation/pause",
    response_model=TelegramAutomationView,
)
def pause_customer_telegram_automation(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramAutomationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_governance_service.pause_automation(project_id, customer_token)
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/customer/workspace/{project_id}/telegram/automation",
    response_model=TelegramAutomationView,
)
def revoke_customer_telegram_automation(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramAutomationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_governance_service.revoke_automation(project_id, customer_token)
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/actions/{action_id}/publish",
    response_model=TelegramClientPublishReceipt,
)
async def publish_customer_telegram_action(
    project_id: UUID,
    action_id: UUID,
    payload: TelegramPublishRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramClientPublishReceipt:
    customer_token = _project_token(session_token, project_id)
    try:
        return await customer_telegram_client_publish_service.publish(
            project_id,
            customer_token,
            action_id,
            payload,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/automation/actions/{action_id}/publish",
    response_model=TelegramClientPublishReceipt,
)
async def automated_publish_customer_telegram_action(
    project_id: UUID,
    action_id: UUID,
    payload: TelegramPublishRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramClientPublishReceipt:
    customer_token = _project_token(session_token, project_id)
    try:
        return await customer_telegram_governance_service.automated_publish(
            project_id,
            customer_token,
            action_id,
            payload,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/actions/{action_id}/observe",
    response_model=TelegramPublishObservationView,
)
async def observe_customer_telegram_action(
    project_id: UUID,
    action_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramPublishObservationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await customer_telegram_governance_service.observe_publish(
            project_id,
            customer_token,
            action_id,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/actions/{action_id}/observation",
    response_model=TelegramPublishObservationView,
)
def get_customer_telegram_action_observation(
    project_id: UUID,
    action_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramPublishObservationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_telegram_governance_service.get_observation(
            project_id,
            customer_token,
            action_id,
        )
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
