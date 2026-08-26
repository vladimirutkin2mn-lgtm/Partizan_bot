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
    customer_funnel_service,
)
from app.customer_schemas import CustomerAutoResearchStatusRequest
from app.growth_autoresearch_loop import growth_autoresearch_loop_service
from app.growth_autoresearch_schemas import GrowthAutoResearchOverviewView

router = APIRouter(tags=["customer-autoresearch"])


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _project_product_id(session_token: str | None, project_id: UUID) -> UUID:
    try:
        _, customer_token = customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(
            status_code=403,
            detail="This project does not belong to this account",
        ) from exc
    product_id = project.get("product_id")
    if not product_id:
        raise HTTPException(
            status_code=409,
            detail="Complete deep research before opening Growth AutoResearch experiments.",
        )
    return UUID(str(product_id))


@router.get(
    "/customer/workspace/{project_id}/autoresearch",
    response_model=GrowthAutoResearchOverviewView,
)
def get_customer_autoresearch(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> GrowthAutoResearchOverviewView:
    product_id = _project_product_id(session_token, project_id)
    return growth_autoresearch_loop_service.overview(product_id)


@router.post(
    "/customer/workspace/{project_id}/autoresearch/status",
    response_model=GrowthAutoResearchOverviewView,
)
def set_customer_autoresearch_status(
    project_id: UUID,
    payload: CustomerAutoResearchStatusRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> GrowthAutoResearchOverviewView:
    product_id = _project_product_id(session_token, project_id)
    try:
        return growth_autoresearch_loop_service.set_paused(
            product_id,
            paused=payload.status == "PAUSED",
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Configure Growth AutoResearch before changing its status.",
        ) from exc
