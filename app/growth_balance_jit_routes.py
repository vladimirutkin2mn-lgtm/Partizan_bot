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
from app.distribution_play_service import distribution_play_service
from app.growth_balance import growth_balance_service
from app.growth_balance_jit_funding import (
    NextMoveFundingPlan,
    growth_balance_jit_funding_service,
)
from app.prefunding_paid_proposal import (
    PrefundingPaidProposal,
    prefunding_paid_proposal_service,
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


class CustomerPrefundingPaidProposalView(BaseModel):
    proposal_id: UUID
    play_id: UUID
    platform: str
    opportunity_title: str
    hypothesis: str
    success_metric: str
    required_acquisition_usd: float = Field(gt=0)
    remaining_acquisition_capacity_usd: float = Field(ge=0)
    topup_amount_usd: float = Field(ge=0)
    management_fee_pct: int = Field(ge=0, le=100)
    funding_required: bool


class CustomerPrefundingPaidProposalCheckoutResponse(CustomerPrefundingPaidProposalView):
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


def _balance_plan(
    *,
    project_id: UUID,
    product_id: UUID,
    project_budget_usd: float,
    required_acquisition_usd: float,
) -> NextMoveFundingPlan:
    analytics = distribution_analytics_service.product_analytics(product_id)
    balance = growth_balance_service.summary(project_id, analytics.total_spend)
    return growth_balance_jit_funding_service.plan(
        required_acquisition_usd=required_acquisition_usd,
        project_budget_usd=project_budget_usd,
        funded_usd=float(balance.funded_usd),
        acquisition_spend_usd=float(balance.acquisition_spend_usd),
        remaining_acquisition_capacity_usd=float(balance.remaining_acquisition_capacity_usd),
        management_fee_pct=int(balance.management_fee_pct),
    )


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

    plan = _balance_plan(
        project_id=project_id,
        product_id=project.product_id,
        project_budget_usd=float(project.budget_usd),
        required_acquisition_usd=float(experiment.budget_cap),
    )
    return plan, experiment


def _proposal_plan(
    project_id: UUID,
    customer_token: str,
    proposal_id: UUID | None = None,
) -> tuple[NextMoveFundingPlan, PrefundingPaidProposal]:
    project = customer_funnel_service.get_project(project_id, customer_token)
    if project.product_id is None:
        raise ValueError("Finish research before funding a paid move")
    if proposal_id is None:
        plays = distribution_play_service.get(project.product_id).plays
        proposal = prefunding_paid_proposal_service.get_or_create(
            project_id=project_id,
            product_id=project.product_id,
            project_budget_usd=float(project.budget_usd),
            plays=plays,
        )
    else:
        proposal = prefunding_paid_proposal_service.require_project(proposal_id, project_id)
        if proposal.product_id != project.product_id:
            raise ValueError("Paid funding proposal product no longer matches this project")

    plan = _balance_plan(
        project_id=project_id,
        product_id=project.product_id,
        project_budget_usd=float(project.budget_usd),
        required_acquisition_usd=float(proposal.budget_cap),
    )
    return plan, proposal


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


def _proposal_view(
    plan: NextMoveFundingPlan,
    proposal: PrefundingPaidProposal,
) -> CustomerPrefundingPaidProposalView:
    return CustomerPrefundingPaidProposalView(
        proposal_id=proposal.id,
        play_id=proposal.play_id,
        platform=proposal.platform.value,
        opportunity_title=proposal.opportunity_title,
        hypothesis=proposal.hypothesis,
        success_metric=proposal.success_metric,
        required_acquisition_usd=plan.required_acquisition_usd,
        remaining_acquisition_capacity_usd=plan.remaining_acquisition_capacity_usd,
        topup_amount_usd=plan.topup_amount_usd,
        management_fee_pct=plan.management_fee_pct,
        funding_required=plan.funding_required,
    )


def _checkout_url(
    *,
    project_id: UUID,
    customer_token: str,
    topup_amount_usd: float,
    request: Request,
    settings: Settings,
) -> str:
    generation, stripe_customer_id, amount_cents = growth_balance_service.prepare_checkout(
        project_id,
        customer_token,
        topup_amount_usd,
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
    return checkout.url


@router.post(
    "/customer/workspace/{project_id}/growth-balance/paid-proposal",
    response_model=CustomerPrefundingPaidProposalView,
)
def get_or_create_prefunding_paid_proposal(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerPrefundingPaidProposalView:
    customer_token = _project_access(session_token, project_id)
    try:
        plan, proposal = _proposal_plan(project_id, customer_token)
        return _proposal_view(plan, proposal)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, KeyError) as exc:
        raise HTTPException(status_code=409, detail="No researched paid play is ready") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/growth-balance/paid-proposals/{proposal_id}/checkout",
    response_model=CustomerPrefundingPaidProposalCheckoutResponse,
)
def create_prefunding_paid_proposal_checkout(
    project_id: UUID,
    proposal_id: UUID,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerPrefundingPaidProposalCheckoutResponse:
    customer_token = _project_access(session_token, project_id)
    try:
        plan, proposal = _proposal_plan(project_id, customer_token, proposal_id)
        view = _proposal_view(plan, proposal)
        checkout_url = None
        if plan.funding_required:
            checkout_url = _checkout_url(
                project_id=project_id,
                customer_token=customer_token,
                topup_amount_usd=plan.topup_amount_usd,
                request=request,
                settings=settings,
            )
        return CustomerPrefundingPaidProposalCheckoutResponse(
            **view.model_dump(),
            checkout_url=checkout_url,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Paid funding proposal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Growth Balance checkout is unavailable") from exc


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
        checkout_url = None
        if plan.funding_required:
            checkout_url = _checkout_url(
                project_id=project_id,
                customer_token=customer_token,
                topup_amount_usd=plan.topup_amount_usd,
                request=request,
                settings=settings,
            )
        return CustomerNextMoveFundingCheckoutResponse(
            **view.model_dump(),
            checkout_url=checkout_url,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Growth Balance checkout is unavailable") from exc
