from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_funnel import CustomerProjectAccessError, CustomerProjectNotFoundError
from app.customer_live_opportunities import (
    CustomerLiveOpportunityView,
    customer_live_opportunity_service,
)
from app.customer_research_communities import (
    CustomerResearchCommunityView,
    customer_research_community_service,
)
from app.customer_telegram_discovery_diagnostics import (
    CustomerTelegramDiscoveryView,
    customer_telegram_discovery_service,
)

router = APIRouter(tags=["customer-live-opportunities"])


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _require_project_access(project_id: UUID, session_token: str | None) -> None:
    try:
        customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(status_code=403, detail="This project does not belong to this account") from exc


@router.get(
    "/customer/workspace/{project_id}/live-opportunities",
    response_model=list[CustomerLiveOpportunityView],
)
def list_customer_live_opportunities(
    project_id: UUID,
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> list[CustomerLiveOpportunityView]:
    _require_project_access(project_id, session_token)
    return customer_live_opportunity_service.list_for_project(project_id)


@router.get(
    "/customer/workspace/{project_id}/research-communities",
    response_model=list[CustomerResearchCommunityView],
)
def list_customer_research_communities(
    project_id: UUID,
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> list[CustomerResearchCommunityView]:
    _require_project_access(project_id, session_token)
    return customer_research_community_service.list_for_project(project_id)


@router.get(
    "/customer/workspace/{project_id}/telegram-discovery",
    response_model=CustomerTelegramDiscoveryView,
)
def get_customer_telegram_discovery(
    project_id: UUID,
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> CustomerTelegramDiscoveryView:
    """Read-only source and retry diagnostics, scoped to the project's owner."""
    _require_project_access(project_id, session_token)
    return customer_telegram_discovery_service.for_project(project_id)
