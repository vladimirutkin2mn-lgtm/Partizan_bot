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
from app.customer_research_communities import (
    CustomerResearchCommunityView,
    customer_research_community_service,
)

router = APIRouter(tags=["customer-research-communities"])


@router.get(
    "/customer/workspace/{project_id}/research-communities",
    response_model=list[CustomerResearchCommunityView],
)
def list_customer_research_communities(
    project_id: UUID,
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> list[CustomerResearchCommunityView]:
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
    return customer_research_community_service.list_for_project(project_id)
