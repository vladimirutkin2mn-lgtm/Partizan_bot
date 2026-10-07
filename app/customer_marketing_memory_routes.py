from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException, Query, status

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_funnel import CustomerProjectAccessError, CustomerProjectNotFoundError
from app.project_marketing_memory import (
    ProjectMarketingMemoryConflictError,
    ProjectMarketingMemoryCustomerCreateRequest,
    ProjectMarketingMemoryEntryView,
    ProjectMarketingMemoryError,
    ProjectMarketingMemoryPromptView,
    ProjectMarketingMemoryView,
    project_marketing_memory_service,
)

router = APIRouter(tags=["customer-marketing-memory"])


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
        raise HTTPException(
            status_code=403,
            detail="This project does not belong to this account",
        ) from exc


@router.get(
    "/customer/workspace/{project_id}/marketing-memory",
    response_model=ProjectMarketingMemoryView,
)
def get_project_marketing_memory(
    project_id: UUID,
    session_token: Annotated[
        str | None,
        Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE),
    ] = None,
) -> ProjectMarketingMemoryView:
    customer_token = _project_token(session_token, project_id)
    try:
        return project_marketing_memory_service.overview(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/marketing-memory",
    response_model=ProjectMarketingMemoryEntryView,
    status_code=status.HTTP_201_CREATED,
)
def add_project_marketing_memory(
    project_id: UUID,
    payload: ProjectMarketingMemoryCustomerCreateRequest,
    session_token: Annotated[
        str | None,
        Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE),
    ] = None,
) -> ProjectMarketingMemoryEntryView:
    customer_token = _project_token(session_token, project_id)
    try:
        return project_marketing_memory_service.add_customer_confirmed(
            project_id,
            customer_token,
            payload,
        )
    except ProjectMarketingMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/customer/workspace/{project_id}/marketing-memory/{entry_id}",
    response_model=ProjectMarketingMemoryEntryView,
)
def retire_project_marketing_memory(
    project_id: UUID,
    entry_id: UUID,
    session_token: Annotated[
        str | None,
        Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE),
    ] = None,
) -> ProjectMarketingMemoryEntryView:
    customer_token = _project_token(session_token, project_id)
    try:
        return project_marketing_memory_service.retire(
            project_id,
            customer_token,
            entry_id,
        )
    except ProjectMarketingMemoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/marketing-memory/prompt-preview",
    response_model=ProjectMarketingMemoryPromptView,
)
def preview_project_marketing_memory_prompt(
    project_id: UUID,
    platform: Annotated[str | None, Query(max_length=40)] = None,
    action_type: Annotated[str | None, Query(max_length=40)] = None,
    session_token: Annotated[
        str | None,
        Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE),
    ] = None,
) -> ProjectMarketingMemoryPromptView:
    customer_token = _project_token(session_token, project_id)
    try:
        return project_marketing_memory_service.prompt_preview(
            project_id,
            customer_token,
            platform=platform,
            action_type=action_type,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
