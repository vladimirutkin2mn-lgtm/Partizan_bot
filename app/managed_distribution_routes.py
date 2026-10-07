from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.distribution_types import DistributionPlatform
from app.managed_distribution import ManagedDistributionError, managed_distribution_service
from app.managed_distribution_schemas import (
    CustomerManagedAssignmentView,
    ManagedAssignmentCreateRequest,
    ManagedAssignmentView,
    ManagedFulfillmentRequest,
    ManagedPublisherHealthRequest,
    ManagedPublisherRegistrationRequest,
    ManagedPublisherView,
    ManagedSelectionCandidateView,
    ManagedSelectionRequest,
)
from app.managed_telegram_execution import (
    ManagedTelegramActionPreview,
    ManagedTelegramActionPreviewRequest,
    ManagedTelegramConnectionView,
    ManagedTelegramExecuteRequest,
    ManagedTelegramExecutionError,
    ManagedTelegramExecutionReceipt,
    ManagedTelegramReconcileRequest,
    ManagedTelegramSessionInstallRequest,
    managed_telegram_execution_service,
)
from app.managed_telegram_profile_strategy import (
    ManagedTelegramProfileStrategyApplyRequest,
    ManagedTelegramProfileStrategyApprovalRequest,
    ManagedTelegramProfileStrategyCreateRequest,
    ManagedTelegramProfileStrategyError,
    ManagedTelegramProfileStrategyPreview,
    ManagedTelegramProfileStrategyRollbackRequest,
    ManagedTelegramProfileStrategyView,
    managed_telegram_profile_strategy_service,
)
from app.operator_auth import require_operator
from app.product_intake import product_intake_service

operator_router = APIRouter(
    tags=["managed-distribution"],
    dependencies=[Depends(require_operator)],
)
customer_router = APIRouter(tags=["customer-managed-distribution"])


@operator_router.post(
    "/managed-distribution/publishers",
    response_model=ManagedPublisherView,
    status_code=status.HTTP_201_CREATED,
)
def register_managed_publisher(
    payload: ManagedPublisherRegistrationRequest,
) -> ManagedPublisherView:
    try:
        return managed_distribution_service.register_publisher(payload)
    except ManagedDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/managed-distribution/publishers",
    response_model=list[ManagedPublisherView],
)
def list_managed_publishers(
    platform: DistributionPlatform | None = None,
) -> list[ManagedPublisherView]:
    return managed_distribution_service.list_publishers(platform)


@operator_router.patch(
    "/managed-distribution/publishers/{publisher_id}/health",
    response_model=ManagedPublisherView,
)
def set_managed_publisher_health(
    publisher_id: UUID,
    payload: ManagedPublisherHealthRequest,
) -> ManagedPublisherView:
    try:
        return managed_distribution_service.set_health(
            publisher_id,
            payload.health,
            payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Managed publisher not found") from exc


@operator_router.put(
    "/managed-distribution/publishers/{publisher_id}/telegram/connection",
    response_model=ManagedTelegramConnectionView,
)
async def install_managed_telegram_connection(
    publisher_id: UUID,
    payload: ManagedTelegramSessionInstallRequest,
) -> ManagedTelegramConnectionView:
    try:
        return await managed_telegram_execution_service.install_session(
            publisher_id,
            payload,
        )
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/managed-distribution/publishers/{publisher_id}/telegram/connection",
    response_model=ManagedTelegramConnectionView,
)
def get_managed_telegram_connection(
    publisher_id: UUID,
) -> ManagedTelegramConnectionView:
    try:
        return managed_telegram_execution_service.connection(publisher_id)
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/publishers/{publisher_id}/telegram/connection/verify",
    response_model=ManagedTelegramConnectionView,
)
async def verify_managed_telegram_connection(
    publisher_id: UUID,
) -> ManagedTelegramConnectionView:
    try:
        return await managed_telegram_execution_service.verify_session(publisher_id)
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.delete(
    "/managed-distribution/publishers/{publisher_id}/telegram/connection",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_managed_telegram_connection(publisher_id: UUID) -> None:
    try:
        managed_telegram_execution_service.disconnect(publisher_id)
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/selection/preview",
    response_model=list[ManagedSelectionCandidateView],
)
def preview_managed_selection(
    payload: ManagedSelectionRequest,
) -> list[ManagedSelectionCandidateView]:
    return managed_distribution_service.select_candidates(payload)


@operator_router.post(
    "/products/{product_id}/managed-distribution/assignments",
    response_model=ManagedAssignmentView,
    status_code=status.HTTP_201_CREATED,
)
def reserve_managed_assignment(
    product_id: UUID,
    payload: ManagedAssignmentCreateRequest,
) -> ManagedAssignmentView:
    try:
        return managed_distribution_service.reserve(product_id, payload)
    except ManagedDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/products/{product_id}/managed-distribution/assignments",
    response_model=list[ManagedAssignmentView],
)
def list_managed_assignments(product_id: UUID) -> list[ManagedAssignmentView]:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return managed_distribution_service.list_assignments(product_id)


@operator_router.post(
    "/managed-distribution/assignments/{assignment_id}/telegram/profile-strategy",
    response_model=ManagedTelegramProfileStrategyView,
    status_code=status.HTTP_201_CREATED,
)
def create_managed_telegram_profile_strategy(
    assignment_id: UUID,
    payload: ManagedTelegramProfileStrategyCreateRequest,
) -> ManagedTelegramProfileStrategyView:
    try:
        return managed_telegram_profile_strategy_service.create(assignment_id, payload)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/managed-distribution/assignments/{assignment_id}/telegram/profile-strategy",
    response_model=ManagedTelegramProfileStrategyView,
)
def get_managed_telegram_profile_strategy(
    assignment_id: UUID,
) -> ManagedTelegramProfileStrategyView:
    strategy = managed_telegram_profile_strategy_service.for_assignment(assignment_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Managed Telegram profile strategy not found")
    return strategy


@operator_router.get(
    "/managed-distribution/telegram/profile-strategies/{strategy_id}/avatar",
)
def get_managed_telegram_profile_strategy_avatar(strategy_id: UUID) -> Response:
    try:
        content, mime_type, _ = managed_telegram_profile_strategy_service.avatar_bytes(
            strategy_id
        )
        return Response(content=content, media_type=mime_type)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@operator_router.get(
    "/managed-distribution/telegram/profile-strategies/{strategy_id}/preview",
    response_model=ManagedTelegramProfileStrategyPreview,
)
async def preview_managed_telegram_profile_strategy(
    strategy_id: UUID,
) -> ManagedTelegramProfileStrategyPreview:
    try:
        return await managed_telegram_profile_strategy_service.preview(strategy_id)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/telegram/profile-strategies/{strategy_id}/approve",
    response_model=ManagedTelegramProfileStrategyView,
)
def approve_managed_telegram_profile_strategy(
    strategy_id: UUID,
    payload: ManagedTelegramProfileStrategyApprovalRequest,
) -> ManagedTelegramProfileStrategyView:
    try:
        return managed_telegram_profile_strategy_service.approve(strategy_id, payload)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/telegram/profile-strategies/{strategy_id}/apply",
    response_model=ManagedTelegramProfileStrategyView,
)
async def apply_managed_telegram_profile_strategy(
    strategy_id: UUID,
    payload: ManagedTelegramProfileStrategyApplyRequest,
) -> ManagedTelegramProfileStrategyView:
    try:
        return await managed_telegram_profile_strategy_service.apply(strategy_id, payload)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/telegram/profile-strategies/{strategy_id}/rollback",
    response_model=ManagedTelegramProfileStrategyView,
)
async def rollback_managed_telegram_profile_strategy(
    strategy_id: UUID,
    payload: ManagedTelegramProfileStrategyRollbackRequest,
) -> ManagedTelegramProfileStrategyView:
    try:
        return await managed_telegram_profile_strategy_service.rollback(strategy_id, payload)
    except ManagedTelegramProfileStrategyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/assignments/{assignment_id}/telegram/preview",
    response_model=ManagedTelegramActionPreview,
)
def preview_managed_telegram_action(
    assignment_id: UUID,
    payload: ManagedTelegramActionPreviewRequest,
) -> ManagedTelegramActionPreview:
    try:
        return managed_telegram_execution_service.preview(assignment_id, payload)
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/assignments/{assignment_id}/telegram/execute",
    response_model=ManagedTelegramExecutionReceipt,
)
async def execute_managed_telegram_action(
    assignment_id: UUID,
    payload: ManagedTelegramExecuteRequest,
) -> ManagedTelegramExecutionReceipt:
    try:
        return await managed_telegram_execution_service.execute(
            assignment_id,
            payload,
        )
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.post(
    "/managed-distribution/assignments/{assignment_id}/telegram/reconcile",
    response_model=ManagedTelegramExecutionReceipt,
)
def reconcile_managed_telegram_action(
    assignment_id: UUID,
    payload: ManagedTelegramReconcileRequest,
) -> ManagedTelegramExecutionReceipt:
    try:
        return managed_telegram_execution_service.reconcile(
            assignment_id,
            payload,
        )
    except ManagedTelegramExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/managed-distribution/assignments/{assignment_id}/telegram/receipt",
    response_model=ManagedTelegramExecutionReceipt,
)
def get_managed_telegram_receipt(
    assignment_id: UUID,
) -> ManagedTelegramExecutionReceipt:
    receipt = managed_telegram_execution_service.receipt(assignment_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Managed Telegram receipt not found")
    return receipt


@operator_router.post(
    "/managed-distribution/assignments/{assignment_id}/fulfill",
    response_model=ManagedAssignmentView,
)
def fulfill_managed_assignment(
    assignment_id: UUID,
    payload: ManagedFulfillmentRequest,
) -> ManagedAssignmentView:
    try:
        return managed_distribution_service.fulfill(assignment_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Managed assignment not found") from exc
    except ManagedDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.delete(
    "/managed-distribution/assignments/{assignment_id}",
    response_model=ManagedAssignmentView,
)
def release_managed_assignment(assignment_id: UUID) -> ManagedAssignmentView:
    try:
        return managed_distribution_service.release(assignment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Managed assignment not found") from exc
    except ManagedDistributionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


@customer_router.get(
    "/customer/workspace/{project_id}/managed-distribution/assignments",
    response_model=list[CustomerManagedAssignmentView],
)
def get_customer_managed_assignments(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> list[CustomerManagedAssignmentView]:
    customer_token = _project_token(session_token, project_id)
    try:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    product_id_raw = project.get("product_id")
    if not product_id_raw:
        return []
    return managed_distribution_service.list_customer_assignments(UUID(str(product_id_raw)))
