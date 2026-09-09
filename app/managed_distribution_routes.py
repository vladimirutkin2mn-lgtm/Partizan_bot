from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, status

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
