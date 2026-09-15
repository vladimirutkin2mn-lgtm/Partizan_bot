from __future__ import annotations

from typing import Annotated
from uuid import UUID

import stripe
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.autonomy_overview import AutonomyExperimentSummary, autonomy_overview_service
from app.config import Settings, get_settings
from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_billing import BillingConfigurationError, create_growth_balance_checkout
from app.customer_funnel import (
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.growth_balance import growth_balance_service
from app.growth_balance_jit_funding import (
    NextMoveFundingPlan,
    growth_balance_jit_funding_service,
)

router = APIRouter(tags=["customer-account"])


class CustomerNextMoveFundingView(BaseModel):
    experiment_id: UUID
    action_id: UUID
    platform: str
    required_acquisition_usd: float = Field(ge=0)
    remaining_acquisition_capacity_usd: float = Field(ge=0)
    topup_amount_usd: float = Field(ge=0)
    management_fee_pct: int = Field(ge=0, le=100)
    funding_required: bool


class CustomerNextMoveFundingCheckoutResponse(CustomerNextMoveFundingView):
    checkout_url: str | None = None


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _project_access(session_token: str | None, project_id: UUID) -> str:
    try:
        _, customer_token = customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(status_code=403, detail="This project does not belong to this account") from exc
    return customer_token


def _funding_plan(
    project_id: UUID,
    customer_token: str,
    experiment_id: UUID,
) -> tuple[NextMoveFundingPlan, AutonomyExperimentSummary]:
    project = customer_funnel_service.get_project(project_id, customer_token)
    if project.product_id is None:
        raise ValueError("A researched product is required before paid-move funding")

    autonomy = autonomy_overview_service.get(project.product_id)
    experiment = next(
        (
            item
            for item in autonomy.waiting_approval
            if item.experiment_id == experiment_id
        ),
        None,
    )
    if experiment is None:
        raise ValueError("Paid experiment is not waiting for execution")
    if experiment.action_type != "PAID_CAMPAIGN" or experiment.budget_cap is None:
        raise ValueError("Experiment does not have an executable paid-campaign budget")

    analytics = distribution_analytics_service.product_analytics(project.product_id)
    balance = growth_balance_service.summary(project_id, analytics.total_spend)
    plan = growth_balance_jit_funding_service.plan(
        required_acquisition_usd=float(experiment.budget_cap),
        project_budget_usd=float(project.budget_usd),
        funded_usd=float(balance.funded_usd),
        acquisition_spend_usd=float(balance.acquisition_spend_usd),
        remaining_acquisition_capacity_usd=float(balance.remaining_acquisition_capacity_usd),
        management_fee_pct=int(balance.management_fee_pct),
    )
    return plan, experiment


def _view(
    plan: NextMoveFundingPlan,
    experiment: AutonomyExperimentSummary,
) -> CustomerNextMoveFundingView:
    return CustomerNextMoveFundingView(
        experiment_id=experiment.experiment_id,
        action_id=experiment.action_id,
        platform=experiment.platform,
        required_acquisition_usd=plan.required_acquisition_usd,
        remaining_acquisition_capacity_usd=plan.remaining_acquisition_capacity_usd,
        topup_amount_usd=plan.topup_amount_usd,
        management_fee_pct=plan.management_fee_pct,
        funding_required=plan.funding_required,
    )


@router.get(
    "/customer/workspace/{project_id}/growth-balance/experiments/{experiment_id}/funding",
    response_model=CustomerNextMoveFundingView,
)
def get_next_move_funding(
    project_id: UUID,
    experiment_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerNextMoveFundingView:
    customer_token = _project_access(session_token, project_id)
    try:
        plan, experiment = _funding_plan(project_id, customer_token, experiment_id)
        return _view(plan, experiment)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/growth-balance/experiments/{experiment_id}/funding/checkout",
    response_model=CustomerNextMoveFundingCheckoutResponse,
)
def create_next_move_funding_checkout(
    project_id: UUID,
    experiment_id: UUID,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerNextMoveFundingCheckoutResponse:
    customer_token = _project_access(session_token, project_id)
    try:
        plan, experiment = _funding_plan(project_id, customer_token, experiment_id)
        view = _view(plan, experiment)
        if not plan.funding_required:
            return CustomerNextMoveFundingCheckoutResponse(
                **view.model_dump(),
                checkout_url=None,
            )

        generation, stripe_customer_id, amount_cents = growth_balance_service.prepare_checkout(
            project_id,
            customer_token,
            plan.topup_amount_usd,
        )
        public_origin = settings.partizan_public_base_url or str(request.base_url).rstrip("/")
        checkout = create_growth_balance_checkout(
            settings=settings,
            project_id=project_id,
            public_origin=public_origin,
            checkout_generation=generation,
            amount_cents=amount_cents,
            stripe_customer_id=stripe_customer_id,
            return_path="/workspace",
        )
        growth_balance_service.mark_checkout_pending(
            project_id,
            customer_token,
            session_id=checkout.session_id,
            amount_cents=amount_cents,
        )
        return CustomerNextMoveFundingCheckoutResponse(
            **view.model_dump(),
            checkout_url=checkout.url,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Growth Balance checkout is unavailable") from exc
