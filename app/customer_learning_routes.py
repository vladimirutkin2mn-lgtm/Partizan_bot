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
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_learning_schemas import (
    CustomerDistributionLearningEntryView,
    CustomerDistributionLearningView,
)
from app.distribution_growth_manager_service import distribution_growth_manager_service

router = APIRouter(tags=["customer-learning"])


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _observed_basis(entry) -> list[str]:
    basis: list[str] = []
    if entry.removals > 0:
        basis.append(
            f"{entry.removals} removal(s) observed on this exact opportunity/action pattern."
        )
    if entry.replies > 0:
        basis.append(f"{entry.replies} community reply/replies observed.")
    if entry.paid_users > 0:
        if entry.observed_cac is not None:
            basis.append(
                f"{entry.paid_users} paid customer(s) observed at CAC ${entry.observed_cac:.2f}."
            )
        else:
            basis.append(f"{entry.paid_users} paid customer(s) observed.")
    elif entry.revenue > 0:
        basis.append(f"Observed attributed revenue ${entry.revenue:.2f}.")
    if not basis:
        basis.append("No paid conversion or community response has been observed yet.")
    return basis


@router.get(
    "/customer/workspace/{project_id}/distribution-learning",
    response_model=CustomerDistributionLearningView,
)
def get_customer_distribution_learning(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerDistributionLearningView:
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
        raise HTTPException(status_code=403, detail="This project does not belong to this account") from exc

    product_id_raw = project.get("product_id")
    if not product_id_raw:
        return CustomerDistributionLearningView(project_id=project_id, entries=[])
    try:
        product_id = UUID(str(product_id_raw))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Customer project product id is invalid") from exc

    try:
        memory = distribution_growth_manager_service.learning_memory(product_id)
    except KeyError:
        return CustomerDistributionLearningView(
            project_id=project_id,
            product_id=product_id,
            entries=[],
        )

    entries: list[CustomerDistributionLearningEntryView] = []
    for entry in sorted(
        memory.entries,
        key=lambda item: (item.created_at, str(item.id)),
        reverse=True,
    )[:12]:
        try:
            opportunity = audience_intelligence_service.find_opportunity(entry.opportunity_id)
        except KeyError:
            opportunity = None
        entries.append(
            CustomerDistributionLearningEntryView(
                experiment_id=entry.experiment_id,
                platform=entry.platform.value,
                opportunity_title=(
                    opportunity.title if opportunity is not None else "Observed opportunity"
                ),
                opportunity_url=(opportunity.url if opportunity is not None else None),
                publisher_mode=entry.publisher_mode.value,
                action_type=(entry.action_type.value if entry.action_type is not None else None),
                decision=entry.action,
                observed_cac=entry.observed_cac,
                paid_users=entry.paid_users,
                revenue=entry.revenue,
                replies=entry.replies,
                removals=entry.removals,
                observed_basis=_observed_basis(entry),
                created_at=entry.created_at,
            )
        )
    return CustomerDistributionLearningView(
        project_id=project_id,
        product_id=product_id,
        entries=entries,
    )
