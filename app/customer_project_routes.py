from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException, status

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    CustomerAccountConflictError,
    customer_account_service,
)
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_project_schemas import (
    CustomerAccountCreateProjectRequest,
    CustomerAccountCreateProjectResponse,
    CustomerAccountProjectNavView,
)
from app.customer_schemas import CustomerPreviewRequest
from app.runtime_store import get_runtime_store

router = APIRouter(tags=["customer-account-projects"])

_PROJECT_TYPE_LABELS = {
    "WEBSITE_PRODUCT": "Website or product",
    "TELEGRAM_COMMUNITY": "Telegram channel or group",
    "SOCIAL_ACCOUNT": "Social account",
    "APP": "App",
    "BUSINESS_SERVICE": "Business or service",
    "OTHER": "Other",
}


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _require_account(session_token: str | None) -> dict:
    try:
        return customer_account_service.account_for_session(session_token)
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get(
    "/customer/account/projects",
    response_model=list[CustomerAccountProjectNavView],
)
def list_customer_account_projects(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> list[CustomerAccountProjectNavView]:
    account = _require_account(session_token)
    store = get_runtime_store()
    projects: list[CustomerAccountProjectNavView] = []
    for project_id_raw in account.get("project_ids", []):
        project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id_raw))
        if project is None:
            continue
        created_at = datetime.fromisoformat(str(project["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        fallback_name = f"{project.get('market') or 'Project'} · {project.get('goal') or 'Acquisition'}"
        projects.append(
            CustomerAccountProjectNavView(
                project_id=UUID(str(project["id"])),
                name=str(project.get("project_name") or fallback_name),
                project_type=project.get("project_type"),
                reference_url=project.get("reference_url") or project.get("website_url"),
                market=str(project.get("market") or ""),
                goal=str(project.get("goal") or ""),
                created_at=created_at,
            )
        )
    projects.sort(key=lambda item: item.created_at, reverse=True)
    return projects


@router.post(
    "/customer/account/projects",
    response_model=CustomerAccountCreateProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer_account_project(
    payload: CustomerAccountCreateProjectRequest,
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> CustomerAccountCreateProjectResponse:
    _require_account(session_token)
    type_label = _PROJECT_TYPE_LABELS[payload.project_type]
    enriched_brief = (
        f"Promoted asset: {payload.name}\n"
        f"Asset type: {type_label}\n\n"
        f"{payload.brief}"
    )
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief=enriched_brief,
            website_url=payload.reference_url,
            market=payload.market,
            goal=payload.goal,
            budget_usd=payload.budget_usd,
        )
    )
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
    if project is None:
        raise HTTPException(status_code=500, detail="New project could not be created")
    project["project_name"] = payload.name
    project["project_type"] = payload.project_type
    project["reference_url"] = str(payload.reference_url) if payload.reference_url else None
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id), project)

    try:
        account = customer_account_service.claim_project(
            session_token=session_token,
            project_id=preview.project_id,
            customer_token=preview.customer_token,
        )
    except (
        CustomerAccountAuthenticationError,
        CustomerAccountConflictError,
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
    ) as exc:
        current = store.get(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
        if current is not None and not current.get("customer_account_id"):
            store.delete(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
        if isinstance(exc, CustomerAccountAuthenticationError):
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return CustomerAccountCreateProjectResponse(project_id=preview.project_id, account=account)
