from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

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
from app.distribution_analytics_service import distribution_analytics_service
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
from app.telegram_native_attribution import (
    TelegramNativeAttributionError,
    TelegramNativeAttributionView,
    telegram_native_attribution_service,
)
from app.telegram_profile_conversion_pack import (
    TelegramProfilePackApplyRequest,
    TelegramProfilePackApprovalRequest,
    TelegramProfilePackCreateRequest,
    TelegramProfilePackError,
    TelegramProfilePackPreview,
    TelegramProfilePackRollbackRequest,
    TelegramProfilePackStatus,
    TelegramProfilePackUpdateRequest,
    TelegramProfilePackView,
    telegram_profile_conversion_pack_service,
)
from app.telegram_profile_learning import (
    TelegramProfileLearningView,
    telegram_profile_learning_service,
)

router = APIRouter(tags=["customer-channels"])


def _refresh_autopilot_channel_policy_best_effort(
    project_id: UUID,
    customer_token: str,
) -> None:
    try:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if project.get("autopilot_pause_reason") != "CUSTOMER":
            customer_autopilot_service.refresh_channel_policy(project_id, customer_token)
    except (KeyError, RuntimeError, ValueError):
        # The Telegram governance mutation remains authoritative and fail-closed.
        # A later channel refresh/reconciliation can rebuild the Growth Mandate.
        return


class CustomerCommunityActionView(BaseModel):
    action_id: UUID
    experiment_id: UUID
    platform: str
    action_type: str
    action_status: str
    experiment_status: str
    publisher_mode: str
    opportunity_title: str
    target_url: str | None = None
    content_text: str | None = None
    replies: int = 0
    removals: int = 0
    execution_outcome: str | None = None
    execution_message: str | None = None
    executed_url: str | None = None
    execution_at: datetime | None = None
    observation_state: str | None = None
    observation_checked_at: datetime | None = None
    profile_pack_id: UUID | None = None
    profile_pack_status: str | None = None
    execution_fee_usd: float = 0.0


class TelegramCustomerPublishRequest(BaseModel):
    confirm_publish: bool = False
    retry: bool = False
    expected_target_url: str | None = None
    expected_content_text: str | None = None


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


def _community_actions(project_id: UUID, customer_token: str) -> list[CustomerCommunityActionView]:
    try:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    product_id_raw = project.get("product_id")
    if not product_id_raw:
        return []
    try:
        analytics = distribution_analytics_service.product_analytics(UUID(str(product_id_raw)))
    except (KeyError, ValueError):
        return []

    result: list[CustomerCommunityActionView] = []
    for item in analytics.experiments:
        platform = item.action.platform.value
        if platform not in {"TELEGRAM", "REDDIT"}:
            continue

        execution_outcome = None
        execution_message = None
        executed_url = None
        execution_at = None
        observation_state = None
        observation_checked_at = None
        profile_pack_id = None
        profile_pack_status = None
        if platform == "TELEGRAM":
            receipt = customer_telegram_client_publish_service.get_receipt(item.action.id)
            if receipt is not None:
                execution_outcome = receipt.outcome.value
                execution_message = receipt.message
                executed_url = str(receipt.executed_url) if receipt.executed_url else None
                execution_at = receipt.published_at or receipt.created_at
            try:
                observation = customer_telegram_governance_service.get_observation_internal(
                    project_id,
                    item.action.id,
                )
            except CustomerTelegramClientPublishError:
                observation = None
            if observation is not None:
                observation_state = observation.latest.state.value
                observation_checked_at = observation.latest.checked_at
            try:
                profile_pack = telegram_profile_conversion_pack_service.for_action(
                    project_id,
                    customer_token,
                    item.action.id,
                )
            except TelegramProfilePackError:
                profile_pack = None
            if profile_pack is not None:
                profile_pack_id = profile_pack.id
                profile_pack_status = profile_pack.status.value

        result.append(
            CustomerCommunityActionView(
                action_id=item.action.id,
                experiment_id=item.experiment.id,
                platform=platform,
                action_type=item.action.action_type.value,
                action_status=item.action.status.value,
                experiment_status=item.experiment.status.value,
                publisher_mode=item.publisher_mode.value,
                opportunity_title=item.play.opportunity_title,
                target_url=(str(item.action.target_url) if item.action.target_url else None),
                content_text=item.action.content_text,
                replies=item.replies,
                removals=item.removals,
                execution_outcome=execution_outcome,
                execution_message=execution_message,
                executed_url=executed_url,
                execution_at=execution_at,
                observation_state=observation_state,
                observation_checked_at=observation_checked_at,
                profile_pack_id=profile_pack_id,
                profile_pack_status=profile_pack_status,
                execution_fee_usd=float(item.costs.execution_fee),
            )
        )
    return result


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


@router.get(
    "/customer/workspace/{project_id}/community-actions",
    response_model=list[CustomerCommunityActionView],
)
def get_customer_community_actions(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[CustomerCommunityActionView]:
    customer_token = _project_token(session_token, project_id)
    return _community_actions(project_id, customer_token)


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
        _refresh_autopilot_channel_policy_best_effort(project_id, customer_token)
        return result
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-packs",
    response_model=list[TelegramProfilePackView],
)
def list_customer_telegram_profile_packs(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[TelegramProfilePackView]:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.list(project_id, customer_token)
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs",
    response_model=TelegramProfilePackView,
    status_code=201,
)
def create_customer_telegram_profile_pack(
    project_id: UUID,
    payload: TelegramProfilePackCreateRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.create(
            project_id,
            customer_token,
            payload,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}",
    response_model=TelegramProfilePackView,
)
def get_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.get(
            project_id,
            customer_token,
            pack_id,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}",
    response_model=TelegramProfilePackView,
)
def update_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    payload: TelegramProfilePackUpdateRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.update_draft(
            project_id,
            customer_token,
            pack_id,
            payload,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/preview",
    response_model=TelegramProfilePackPreview,
)
async def preview_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackPreview:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_profile_conversion_pack_service.preview(
            project_id,
            customer_token,
            pack_id,
        )
    except (TelegramProfilePackError, CustomerTelegramClientPublishError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/avatar",
)
def get_customer_telegram_profile_pack_avatar(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> Response:
    customer_token = _project_token(session_token, project_id)
    try:
        content, mime_type, _ = telegram_profile_conversion_pack_service.avatar_bytes(
            project_id,
            customer_token,
            pack_id,
        )
        return Response(content=content, media_type=mime_type)
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/approve",
    response_model=TelegramProfilePackView,
)
def approve_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    payload: TelegramProfilePackApprovalRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.approve(
            project_id,
            customer_token,
            pack_id,
            payload,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/apply",
    response_model=TelegramProfilePackView,
)
async def apply_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    payload: TelegramProfilePackApplyRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_profile_conversion_pack_service.apply(
            project_id,
            customer_token,
            pack_id,
            payload,
        )
    except (TelegramProfilePackError, CustomerTelegramClientPublishError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/rollback",
    response_model=TelegramProfilePackView,
)
async def rollback_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    payload: TelegramProfilePackRollbackRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_profile_conversion_pack_service.rollback(
            project_id,
            customer_token,
            pack_id,
            payload,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}",
    response_model=TelegramProfilePackView,
)
def archive_customer_telegram_profile_pack(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfilePackView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_conversion_pack_service.archive(
            project_id,
            customer_token,
            pack_id,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/provision",
    response_model=TelegramNativeAttributionView,
)
async def provision_customer_telegram_native_attribution(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramNativeAttributionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_native_attribution_service.provision_channel_invite(
            project_id,
            customer_token,
            pack_id,
        )
    except (
        TelegramNativeAttributionError,
        TelegramProfilePackError,
        CustomerTelegramClientPublishError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/provision-bot",
    response_model=TelegramNativeAttributionView,
)
async def provision_customer_telegram_bot_start_attribution(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramNativeAttributionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_native_attribution_service.provision_bot_start(
            project_id,
            customer_token,
            pack_id,
        )
    except (
        TelegramNativeAttributionError,
        TelegramProfilePackError,
        CustomerTelegramClientPublishError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution",
    response_model=TelegramNativeAttributionView,
)
def get_customer_telegram_native_attribution(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramNativeAttributionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_native_attribution_service.get(
            project_id,
            customer_token,
            pack_id,
        )
    except (TelegramNativeAttributionError, TelegramProfilePackError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/sync",
    response_model=TelegramNativeAttributionView,
)
async def sync_customer_telegram_native_attribution(
    project_id: UUID,
    pack_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramNativeAttributionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return await telegram_native_attribution_service.sync(
            project_id,
            customer_token,
            pack_id,
        )
    except (
        TelegramNativeAttributionError,
        TelegramProfilePackError,
        CustomerTelegramClientPublishError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/telegram/profile-learning",
    response_model=TelegramProfileLearningView,
)
def get_customer_telegram_profile_learning(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramProfileLearningView:
    customer_token = _project_token(session_token, project_id)
    try:
        return telegram_profile_learning_service.overview(project_id, customer_token)
    except TelegramProfilePackError as exc:
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
        result = customer_telegram_governance_service.authorize_automation(
            project_id,
            customer_token,
            payload,
        )
        _refresh_autopilot_channel_policy_best_effort(project_id, customer_token)
        return result
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
        result = customer_telegram_governance_service.pause_automation(
            project_id,
            customer_token,
        )
        _refresh_autopilot_channel_policy_best_effort(project_id, customer_token)
        return result
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
        result = customer_telegram_governance_service.revoke_automation(
            project_id,
            customer_token,
        )
        _refresh_autopilot_channel_policy_best_effort(project_id, customer_token)
        return result
    except CustomerTelegramClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/telegram/actions/{action_id}/publish",
    response_model=TelegramClientPublishReceipt,
)
async def publish_customer_telegram_action(
    project_id: UUID,
    action_id: UUID,
    payload: TelegramCustomerPublishRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> TelegramClientPublishReceipt:
    customer_token = _project_token(session_token, project_id)
    if not payload.confirm_publish:
        raise HTTPException(status_code=409, detail="Explicit publish confirmation is required")
    reviewed = next(
        (item for item in _community_actions(project_id, customer_token) if item.action_id == action_id),
        None,
    )
    try:
        profile_pack = telegram_profile_conversion_pack_service.for_action(
            project_id,
            customer_token,
            action_id,
        )
    except TelegramProfilePackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if profile_pack is not None and profile_pack.status != TelegramProfilePackStatus.APPLIED:
        raise HTTPException(
            status_code=409,
            detail="Apply the reviewed Telegram profile pack before publishing this action",
        )
    if (
        reviewed is None
        or payload.expected_target_url is None
        or payload.expected_content_text is None
        or payload.expected_target_url != str(reviewed.target_url or "")
        or payload.expected_content_text != str(reviewed.content_text or "")
    ):
        raise HTTPException(
            status_code=409,
            detail="Reviewed Telegram action changed; refresh and review it again",
        )
    try:
        return await customer_telegram_client_publish_service.publish(
            project_id,
            customer_token,
            action_id,
            TelegramPublishRequest(retry=payload.retry),
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
