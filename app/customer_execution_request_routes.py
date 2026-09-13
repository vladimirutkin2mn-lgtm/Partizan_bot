from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_channels import customer_channel_service
from app.customer_execution_request_schemas import (
    CustomerExecutionPreparationLinkRequest,
    CustomerExecutionRequestCreate,
    CustomerExecutionRequestView,
)
from app.customer_execution_requests import customer_execution_request_service
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_starting_move_draft import customer_starting_move_draft_service
from app.customer_starting_move_setup import customer_starting_move_setup_service
from app.distribution_play_service import distribution_play_service
from app.operator_auth import require_operator

customer_router = APIRouter(tags=["customer-execution-request"])
operator_router = APIRouter(
    prefix="/v1",
    tags=["customer-execution-request-operator"],
    dependencies=[Depends(require_operator)],
)


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


def _request_context(
    session_token: str | None,
    project_id: UUID,
) -> tuple[dict, object | None, object | None]:
    customer_token = _project_token(session_token, project_id)
    project = customer_funnel_service.get_project_payload(project_id, customer_token)
    draft = customer_starting_move_draft_service.view(project)
    selected = next(
        (
            channel
            for channel in customer_channel_service.list(project_id, customer_token)
            if channel.selected
        ),
        None,
    )
    setup = customer_starting_move_setup_service.view(
        project_id=project_id,
        draft=draft,
        channel=selected,
    )
    return project, draft, setup


@customer_router.get(
    "/customer/workspace/{project_id}/starting-move/execution-request",
    response_model=CustomerExecutionRequestView | None,
)
def get_customer_execution_request(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerExecutionRequestView | None:
    project, draft, _ = _request_context(session_token, project_id)
    return customer_execution_request_service.view(project=project, draft=draft)


@customer_router.post(
    "/customer/workspace/{project_id}/starting-move/execution-request",
    response_model=CustomerExecutionRequestView,
)
def request_customer_execution_preparation(
    project_id: UUID,
    payload: CustomerExecutionRequestCreate,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerExecutionRequestView:
    project, draft, setup = _request_context(session_token, project_id)
    try:
        return customer_execution_request_service.request(
            project=project,
            draft=draft,
            setup=setup,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@operator_router.get(
    "/customer-execution-requests",
    response_model=list[CustomerExecutionRequestView],
)
def list_customer_execution_requests() -> list[CustomerExecutionRequestView]:
    return customer_execution_request_service.list_requests()


@operator_router.post(
    "/customer-execution-requests/{request_id}/preparation-link",
    response_model=CustomerExecutionRequestView,
)
def link_customer_execution_preparation(
    request_id: UUID,
    payload: CustomerExecutionPreparationLinkRequest,
) -> CustomerExecutionRequestView:
    try:
        request = customer_execution_request_service.get_request(request_id)
        play = distribution_play_service.find(request.product_id, payload.distribution_play_id)
        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        return customer_execution_request_service.link_preparation(
            request_id=request_id,
            play=play,
            opportunity=opportunity,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Execution request, DistributionPlay or opportunity not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
