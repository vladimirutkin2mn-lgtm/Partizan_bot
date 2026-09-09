from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

import stripe
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.customer_access_recovery import recover_paid_customer_access
from app.customer_autopilot import customer_autopilot_service
from app.customer_billing import (
    BillingConfigurationError,
    construct_stripe_event,
    create_growth_balance_checkout,
    create_launch_checkout,
    retrieve_launch_checkout,
)
from app.customer_funnel import (
    CUSTOMER_TOKEN_HEADER,
    CustomerPaymentRequiredError,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_meta_oauth import CustomerMetaOAuthError, customer_meta_oauth_service
from app.customer_schemas import (
    CheckoutResponse,
    CustomerAccessRecoveryRequest,
    CustomerAccessRecoveryResponse,
    CustomerAutopilotConfigureRequest,
    CustomerAutopilotOverview,
    CustomerAutopilotStatusRequest,
    CustomerClarificationAnswerRequest,
    CustomerGrowthBalanceTopUpRequest,
    CustomerGrowthBalanceVerifyRequest,
    CustomerMetaConnectionRequest,
    CustomerMetaConnectResponse,
    CustomerMetaOptionsView,
    CustomerPreviewConfirmationResponse,
    CustomerPreviewConfirmRequest,
    CustomerPreviewRequest,
    CustomerPreviewResponse,
    CustomerProductClarificationAnswerRequest,
    CustomerProjectView,
    CustomerResearchResponse,
)
from app.growth_balance import growth_balance_service
from app.product_source import ProductSourceReadError
from app.self_dogfood import SELF_DOGFOOD_ATTRIBUTION_COOKIE, self_dogfood_service
from app.stripe_objects import stripe_field

router = APIRouter(prefix="/v1", tags=["customer"])


def _stripe_customer_id(source: object) -> str | None:
    """Read the Stripe customer id off a Session or webhook object, if Stripe set one."""

    customer = stripe_field(source, "customer")
    if not customer:
        return None
    return str(stripe_field(customer, "id", customer))


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
        detail = str(exc)
        try:
            UUID(detail)
        except ValueError:
            pass
        else:
            detail = (
                "The full market map is a separate research upgrade. "
                "Your free researched opportunity stays available; acquisition budget is only needed "
                "for a concrete paid move."
            )
        return HTTPException(status_code=402, detail=detail)
    return HTTPException(status_code=409, detail=str(exc))


def _meta_callback_target(
    *,
    state: str | None,
    result: str,
    project_id: UUID | None = None,
    return_path: str | None = None,
) -> str:
    if return_path is None and state:
        context = customer_meta_oauth_service.pending_context(state)
        if context is not None:
            context_project_id, context_return_path = context
            project_id = project_id or context_project_id
            return_path = context_return_path
    safe_return_path = return_path if return_path in {"/start", "/workspace"} else "/start"
    query_payload = {"meta": result}
    if project_id is not None:
        query_payload["project"] = str(project_id)
    return f"{safe_return_path}?{urlencode(query_payload)}"


@router.post(
    "/customer-projects/preview",
    response_model=CustomerPreviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer_preview(
    payload: CustomerPreviewRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    self_dogfood_cookie: Annotated[
        str | None,
        Cookie(alias=SELF_DOGFOOD_ATTRIBUTION_COOKIE),
    ] = None,
) -> CustomerPreviewResponse:
    try:
        preview = await customer_funnel_service.create_smart_preview(payload)
    except ProductSourceReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    self_dogfood_service.bind_project_best_effort(
        preview.project_id,
        self_dogfood_cookie,
        settings,
    )
    return preview


@router.post(
    "/customer-projects/{project_id}/product-clarification",
    response_model=CustomerPreviewResponse,
)
async def answer_customer_product_clarification(
    project_id: UUID,
    payload: CustomerProductClarificationAnswerRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerPreviewResponse:
    try:
        token = _require_customer_token(customer_token)
        if payload.answer is not None:
            return await customer_funnel_service.answer_product_clarification(
                project_id,
                token,
                payload.answer,
            )
        return customer_funnel_service.apply_product_context(
            project_id,
            token,
            product=payload.product or "",
            what_it_does=payload.what_it_does or "",
            likely_customer=payload.likely_customer or "",
            business_model=payload.business_model,
            market=payload.market,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/customer-projects/{project_id}/confirm-preview",
    response_model=CustomerPreviewConfirmationResponse,
)
async def confirm_customer_preview(
    project_id: UUID,
    payload: CustomerPreviewConfirmRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerPreviewConfirmationResponse:
    try:
        return await customer_funnel_service.confirm_preview(
            project_id,
            _require_customer_token(customer_token),
            payload,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    "/customer-projects/{project_id}/recover-access",
    response_model=CustomerAccessRecoveryResponse,
)
def recover_customer_access(
    project_id: UUID,
    payload: CustomerAccessRecoveryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CustomerAccessRecoveryResponse:
    try:
        session = retrieve_launch_checkout(settings=settings, session_id=payload.session_id)
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Stripe payment verification is temporarily unavailable",
        ) from exc

    metadata = stripe_field(session, "metadata")
    verified = (
        str(stripe_field(session, "id", "")) == payload.session_id
        and stripe_field(session, "payment_status") == "paid"
        and str(stripe_field(session, "client_reference_id", "")) == str(project_id)
        and str(stripe_field(metadata, "partizan_project_id", "")) == str(project_id)
        and stripe_field(metadata, "partizan_entitlement") == "launch_plan"
    )
    if not verified:
        raise HTTPException(status_code=401, detail="Paid Checkout Session could not be verified")

    unlocked = customer_funnel_service.unlock_launch(
        project_id,
        stripe_checkout_session_id=payload.session_id,
        stripe_customer_id=_stripe_customer_id(session),
    )
    if not unlocked:
        raise HTTPException(status_code=401, detail="Paid Checkout Session is not linked to this project")

    self_dogfood_service.record_project_event_best_effort(
        project_id,
        event_type="PAID",
        business_key=f"stripe-launch:{payload.session_id}",
        settings=settings,
        revenue=round(float(stripe_field(session, "amount_total", 0)) / 100, 2),
    )

    try:
        customer_token = recover_paid_customer_access(project_id, payload.session_id)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    return CustomerAccessRecoveryResponse(project_id=project_id, customer_token=customer_token)


@router.post(
    "/customer-projects/{project_id}/preview-research",
    response_model=CustomerPreviewConfirmationResponse,
)
async def continue_customer_preview_research(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerPreviewConfirmationResponse:
    try:
        return await customer_funnel_service.continue_preview_research(
            project_id,
            _require_customer_token(customer_token),
        )
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
    ) as exc:
        raise _project_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.post(
    "/customer-projects/{project_id}/growth-balance/checkout",
    response_model=CheckoutResponse,
)
def create_growth_balance_topup_checkout(
    project_id: UUID,
    payload: CustomerGrowthBalanceTopUpRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CheckoutResponse:
    token = _require_customer_token(customer_token)
    try:
        generation, stripe_customer_id, amount_cents = growth_balance_service.prepare_checkout(
            project_id,
            token,
            payload.amount_usd,
        )
        public_origin = settings.partizan_public_base_url or str(request.base_url).rstrip("/")
        checkout = create_growth_balance_checkout(
            settings=settings,
            project_id=project_id,
            public_origin=public_origin,
            checkout_generation=generation,
            amount_cents=amount_cents,
            stripe_customer_id=stripe_customer_id,
        )
        growth_balance_service.mark_checkout_pending(
            project_id,
            token,
            session_id=checkout.session_id,
            amount_cents=amount_cents,
        )
        return CheckoutResponse(checkout_url=checkout.url)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, CustomerPaymentRequiredError) as exc:
        raise _project_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Secure acquisition-budget checkout is unavailable",
        ) from exc


@router.post(
    "/customer-projects/{project_id}/growth-balance/verify",
    response_model=CustomerAutopilotOverview,
)
def verify_growth_balance_topup(
    project_id: UUID,
    payload: CustomerGrowthBalanceVerifyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerAutopilotOverview:
    token = _require_customer_token(customer_token)
    try:
        customer_funnel_service.get_project_payload(project_id, token)
        pending = growth_balance_service.pending(payload.session_id)
        if pending is None:
            raise HTTPException(status_code=401, detail="Acquisition-budget checkout session is not pending")
        session = retrieve_launch_checkout(settings=settings, session_id=payload.session_id)
        metadata = stripe_field(session, "metadata")
        amount_total = int(stripe_field(session, "amount_total", 0))
        currency = str(stripe_field(session, "currency", "")).lower()
        verified = (
            str(stripe_field(session, "id", "")) == payload.session_id
            and str(stripe_field(session, "client_reference_id", "")) == str(project_id)
            and str(stripe_field(metadata, "partizan_project_id", "")) == str(project_id)
            and stripe_field(metadata, "partizan_entitlement") == "growth_balance_topup"
            and int(stripe_field(metadata, "partizan_amount_cents", 0))
            == int(pending.get("amount_cents") or 0)
            and stripe_field(session, "mode") == "payment"
            and stripe_field(session, "payment_status") == "paid"
            and amount_total == int(pending.get("amount_cents") or 0)
            and currency == "usd"
        )
        if not verified:
            raise HTTPException(status_code=401, detail="Acquisition-budget payment could not be verified")
        credited = growth_balance_service.credit_paid_checkout(
            project_id,
            session_id=payload.session_id,
            amount_cents=amount_total,
            currency=currency,
            stripe_customer_id=_stripe_customer_id(session),
        )
        if not credited:
            raise HTTPException(
                status_code=401,
                detail="Acquisition-budget payment is not linked to this project",
            )
        return customer_autopilot_service.overview(project_id, token)
    except HTTPException:
        raise
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Acquisition-budget payment verification failed") from exc


@router.put(
    "/customer-projects/{project_id}/autopilot",
    response_model=CustomerAutopilotOverview,
)
def configure_customer_autopilot(
    project_id: UUID,
    payload: CustomerAutopilotConfigureRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerAutopilotOverview:
    try:
        return customer_autopilot_service.configure(
            project_id,
            _require_customer_token(customer_token),
            payload,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, CustomerPaymentRequiredError) as exc:
        raise _project_error(exc) from exc
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer-projects/{project_id}/autopilot",
    response_model=CustomerAutopilotOverview,
)
def get_customer_autopilot(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerAutopilotOverview:
    try:
        return customer_autopilot_service.overview(project_id, _require_customer_token(customer_token))
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer-projects/{project_id}/autopilot/status",
    response_model=CustomerAutopilotOverview,
)
def set_customer_autopilot_status(
    project_id: UUID,
    payload: CustomerAutopilotStatusRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerAutopilotOverview:
    try:
        return customer_autopilot_service.set_status(
            project_id,
            _require_customer_token(customer_token),
            payload.status,
        )
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, CustomerPaymentRequiredError) as exc:
        raise _project_error(exc) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer-projects/{project_id}/autopilot/meta/connect",
    response_model=CustomerMetaConnectResponse,
)
def begin_customer_meta_oauth(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerMetaConnectResponse:
    try:
        url = customer_meta_oauth_service.begin(project_id, _require_customer_token(customer_token))
        return CustomerMetaConnectResponse(authorization_url=url)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except CustomerMetaOAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/customer-meta/oauth/callback")
def complete_customer_meta_oauth(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error or not state or not code:
        return RedirectResponse(
            url=_meta_callback_target(state=state, result="error"),
            status_code=303,
        )
    try:
        project_id, return_path = customer_meta_oauth_service.complete_with_return(
            state=state,
            code=code,
        )
    except CustomerMetaOAuthError:
        return RedirectResponse(
            url=_meta_callback_target(state=state, result="error"),
            status_code=303,
        )
    return RedirectResponse(
        url=_meta_callback_target(
            state=state,
            result="connected",
            project_id=project_id,
            return_path=return_path,
        ),
        status_code=303,
    )


@router.get(
    "/customer-projects/{project_id}/autopilot/meta/options",
    response_model=CustomerMetaOptionsView,
)
def get_customer_meta_options(
    project_id: UUID,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerMetaOptionsView:
    try:
        return customer_meta_oauth_service.options(project_id, _require_customer_token(customer_token))
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc


@router.post(
    "/customer-projects/{project_id}/autopilot/meta/connection",
    response_model=CustomerAutopilotOverview,
)
def save_customer_meta_connection(
    project_id: UUID,
    payload: CustomerMetaConnectionRequest,
    customer_token: Annotated[str | None, Header(alias=CUSTOMER_TOKEN_HEADER)] = None,
) -> CustomerAutopilotOverview:
    token = _require_customer_token(customer_token)
    try:
        customer_meta_oauth_service.connect(project_id, token, payload)
        return customer_autopilot_service.meta_connected(project_id, token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _project_error(exc) from exc
    except (CustomerMetaOAuthError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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

    event_type = str(stripe_field(event, "type", ""))
    obj = event["data"]["object"]
    metadata = stripe_field(obj, "metadata")
    project_id_raw = stripe_field(metadata, "partizan_project_id")
    entitlement = stripe_field(metadata, "partizan_entitlement")

    if event_type == "checkout.session.completed" and project_id_raw:
        try:
            project_id = UUID(str(project_id_raw))
        except ValueError:
            project_id = None
        if (
            project_id is not None
            and entitlement == "launch_plan"
            and stripe_field(obj, "payment_status") == "paid"
        ):
            unlocked = customer_funnel_service.unlock_launch(
                project_id,
                stripe_checkout_session_id=str(stripe_field(obj, "id", "")),
                stripe_customer_id=_stripe_customer_id(obj),
            )
            if unlocked:
                self_dogfood_service.record_project_event_best_effort(
                    project_id,
                    event_type="PAID",
                    business_key=f"stripe-launch:{stripe_field(obj, 'id', '')}",
                    settings=settings,
                    revenue=round(float(stripe_field(obj, "amount_total", 0)) / 100, 2),
                )
        elif (
            project_id is not None
            and entitlement == "growth_balance_topup"
            and stripe_field(obj, "payment_status") == "paid"
        ):
            growth_balance_service.credit_paid_checkout(
                project_id,
                session_id=str(stripe_field(obj, "id", "")),
                amount_cents=int(stripe_field(obj, "amount_total", 0)),
                currency=str(stripe_field(obj, "currency", "")),
                stripe_customer_id=_stripe_customer_id(obj),
            )

    return {"received": True}
