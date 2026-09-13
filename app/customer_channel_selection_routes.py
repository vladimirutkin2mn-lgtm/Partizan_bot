from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.broad_research import PreviewResearchUnavailableError
from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_channel_schemas import (
    CustomerChannelSelectionRequest,
    CustomerChannelView,
    CustomerStartingMoveDraftEditRequest,
    CustomerStartingMoveDraftView,
    CustomerStartingMoveView,
)
from app.customer_channels import customer_channel_service
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_starting_move import customer_starting_move_service
from app.customer_starting_move_draft import customer_starting_move_draft_service

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


def _project(session_token: str | None, project_id: UUID) -> dict:
    customer_token = _project_token(session_token, project_id)
    return customer_funnel_service.get_project_payload(project_id, customer_token)


@router.get(
    "/customer/workspace/{project_id}/starting-move",
    response_model=CustomerStartingMoveView | None,
)
def get_customer_starting_move(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveView | None:
    return customer_starting_move_service.view(_project(session_token, project_id))


@router.post(
    "/customer/workspace/{project_id}/starting-move/research",
    response_model=CustomerStartingMoveView,
)
async def research_customer_starting_move(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveView:
    project = _project(session_token, project_id)
    try:
        return await customer_starting_move_service.research(project)
    except PreviewResearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/starting-move/draft",
    response_model=CustomerStartingMoveDraftView | None,
)
def get_customer_starting_move_draft(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveDraftView | None:
    return customer_starting_move_draft_service.view(_project(session_token, project_id))


@router.post(
    "/customer/workspace/{project_id}/starting-move/draft",
    response_model=CustomerStartingMoveDraftView,
)
async def prepare_customer_starting_move_draft(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveDraftView:
    project = _project(session_token, project_id)
    try:
        return await customer_starting_move_draft_service.prepare(project)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/customer/workspace/{project_id}/starting-move/draft",
    response_model=CustomerStartingMoveDraftView,
)
def edit_customer_starting_move_draft(
    project_id: UUID,
    payload: CustomerStartingMoveDraftEditRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveDraftView:
    project = _project(session_token, project_id)
    try:
        return customer_starting_move_draft_service.edit(project, payload)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/starting-move/draft/accept",
    response_model=CustomerStartingMoveDraftView,
)
def accept_customer_starting_move_draft(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveDraftView:
    project = _project(session_token, project_id)
    try:
        return customer_starting_move_draft_service.accept(project)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/starting-move/draft/reject",
    response_model=CustomerStartingMoveDraftView,
)
def reject_customer_starting_move_draft(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerStartingMoveDraftView:
    project = _project(session_token, project_id)
    try:
        return customer_starting_move_draft_service.reject(project)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
