from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.customer_account_routes import _account_error, _project_access, _session_cookie
from app.customer_funnel import CustomerProjectAccessError, CustomerProjectNotFoundError
from app.customer_meta_guided_setup import (
    CustomerMetaGuidedConnectResponse,
    CustomerMetaGuidedSetupView,
    customer_meta_guided_setup_service,
)
from app.customer_meta_oauth import CustomerMetaOAuthError

router = APIRouter(tags=["customer-meta-guided-setup"])


@router.post(
    "/customer/workspace/{project_id}/meta-guided/connect",
    response_model=CustomerMetaGuidedConnectResponse,
)
def begin_guided_meta_setup(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerMetaGuidedConnectResponse:
    _, customer_token = _project_access(session_token, project_id)
    try:
        url = customer_meta_guided_setup_service.begin(project_id, customer_token)
        return CustomerMetaGuidedConnectResponse(authorization_url=url)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc
    except CustomerMetaOAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/meta-guided/setup",
    response_model=CustomerMetaGuidedSetupView,
)
def get_guided_meta_setup(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerMetaGuidedSetupView:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return customer_meta_guided_setup_service.view(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/meta-guided/check",
    response_model=CustomerMetaGuidedSetupView,
)
def check_guided_meta_setup(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerMetaGuidedSetupView:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return customer_meta_guided_setup_service.check(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc
