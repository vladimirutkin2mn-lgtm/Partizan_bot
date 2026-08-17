from __future__ import annotations

from typing import Annotated
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.customer_billing import (
    BillingConfigurationError,
    construct_stripe_event,
    create_launch_checkout,
)
from app.customer_funnel import (
    CUSTOMER_TOKEN_HEADER,
    CustomerPaymentRequiredError,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_schemas import (
    CheckoutResponse,
    CustomerClarificationAnswerRequest,
    CustomerPreviewRequest,
    CustomerPreviewResponse,
    CustomerProjectView,
    CustomerResearchResponse,
)

router = APIRouter(prefix="/v1", tags=["customer"])


def _require_customer_token(customer_token: str | None) -> str:
    if not customer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer project token required",
        )
    return customer_token


def _project_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CustomerProjectNotFoundError):
        return HTTPException(status_code=404, detail="Customer project not found")
    if isinstance(exc, CustomerProjectAccessError):
        return HTTPException(status_code=401, detail="Customer project token is invalid")
    if isinstance(exc, CustomerPaymentRequiredError):
        return HTTPException(status_code=402, detail="Unlock the acquisition plan before deep research")
    return HTTPException(status_code=409, detail=str(exc))


@router.post(
    "/customer-projects/preview",
    response_model=CustomerPreviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer_preview(payload: CustomerPreviewRequest) -> CustomerPreviewResponse:
    return customer_funnel_service.create_preview(payload)


@router.get("/customer-projects/{project_id}", response_model=CustomerProjectView)
def get_customer_project(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerProjectView:
    try:
        return customer_funnel_service.get_project(
            project_id,
            _require_customer_token(customer_token),
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc


@router.post("/customer-projects/{project_id}/checkout", response_model=CheckoutResponse)
def create_customer_checkout(
    project_id: UUID,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CheckoutResponse:
    token = _require_customer_token(customer_token)
    try:
        project = customer_funnel_service.get_project(project_id, token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    if project.launch_unlocked:
        return CheckoutResponse(already_unlocked=True)

    public_origin = settings.partizan_public_base_url or str(request.base_url).rstrip("/")
    try:
        checkout = create_launch_checkout(
            settings=settings,
            project_id=project_id,
            public_origin=public_origin,
        )
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Checkout is temporarily unavailable") from exc

    customer_funnel_service.mark_checkout_pending(project_id, token, checkout.session_id)
    return CheckoutResponse(checkout_url=checkout.url)


@router.post(
    "/customer-projects/{project_id}/deep-research",
    response_model=CustomerResearchResponse,
)
async def start_customer_deep_research(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerResearchResponse:
    try:
        return await customer_funnel_service.start_deep_research(
            project_id,
            _require_customer_token(customer_token),
        )
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
        CustomerPaymentRequiredError,
    ) as exc:
        raise _project_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/customer-projects/{project_id}/clarifications",
    response_model=CustomerResearchResponse,
)
async def answer_customer_clarification(
    project_id: UUID,
    payload: CustomerClarificationAnswerRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerResearchResponse:
    try:
        return await customer_funnel_service.answer_clarification(
            project_id,
            _require_customer_token(customer_token),
            payload,
        )
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
        CustomerPaymentRequiredError,
        KeyError,
    ) as exc:
        raise _project_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/billing/stripe/webhook")
async def stripe_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, bool]:
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Stripe-Signature header required")
    payload = await request.body()
    try:
        event = construct_stripe_event(
            settings=settings,
            payload=payload,
            signature=stripe_signature,
        )
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature") from exc

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata") or {}
        project_id_raw = metadata.get("partizan_project_id")
        entitlement = metadata.get("partizan_entitlement")
        if project_id_raw and entitlement == "launch_plan" and session.get("payment_status") == "paid":
            try:
                project_id = UUID(str(project_id_raw))
            except ValueError:
                project_id = None
            if project_id is not None:
                customer_funnel_service.unlock_launch(
                    project_id,
                    stripe_checkout_session_id=str(session["id"]),
                    stripe_customer_id=(str(session["customer"]) if session.get("customer") else None),
                )

    return {"received": True}
