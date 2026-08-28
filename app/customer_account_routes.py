from __future__ import annotations

from typing import Annotated
from uuid import UUID

import stripe
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.config import Settings, get_settings
from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CUSTOMER_ACCOUNT_SESSION_DAYS,
    CustomerAccountAuthenticationError,
    CustomerAccountConflictError,
    customer_account_service,
)
from app.customer_account_schemas import (
    CustomerAccountClaimProjectRequest,
    CustomerAccountLoginRequest,
    CustomerAccountRegisterRequest,
    CustomerAccountView,
    CustomerWorkspaceView,
)
from app.customer_autopilot import customer_autopilot_service
from app.customer_billing import (
    BillingConfigurationError,
    create_growth_balance_checkout,
    retrieve_launch_checkout,
)
from app.customer_funnel import (
    CustomerPaymentRequiredError,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.customer_meta_oauth import CustomerMetaOAuthError, customer_meta_oauth_service
from app.customer_schemas import (
    CheckoutResponse,
    CustomerAutopilotConfigureRequest,
    CustomerAutopilotOverview,
    CustomerAutopilotStatusRequest,
    CustomerClarificationAnswerRequest,
    CustomerGrowthBalanceTopUpRequest,
    CustomerGrowthBalanceVerifyRequest,
    CustomerMetaConnectionRequest,
    CustomerMetaConnectResponse,
    CustomerMetaOptionsView,
    CustomerResearchResponse,
)
from app.growth_balance import growth_balance_service
from app.self_dogfood import self_dogfood_service

router = APIRouter(tags=["customer-account"])


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=CUSTOMER_ACCOUNT_SESSION_COOKIE,
        value=token,
        max_age=CUSTOMER_ACCOUNT_SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=settings.app_env.strip().lower() in {"prod", "production"},
        samesite="lax",
        path="/",
    )


def _account_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CustomerAccountAuthenticationError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, CustomerAccountConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, CustomerProjectNotFoundError):
        return HTTPException(status_code=404, detail="Customer project not found")
    if isinstance(exc, CustomerProjectAccessError):
        return HTTPException(status_code=403, detail="This project does not belong to this account")
    if isinstance(exc, CustomerPaymentRequiredError):
        return HTTPException(
            status_code=402,
            detail="Fund Growth Balance before starting the included deep research.",
        )
    return HTTPException(status_code=409, detail=str(exc))


def _project_access(session_token: str | None, project_id: UUID) -> tuple[CustomerAccountView, str]:
    try:
        return customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
    except (
        CustomerAccountAuthenticationError,
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
    ) as exc:
        raise _account_error(exc) from exc


@router.post("/customer/account/register", response_model=CustomerAccountView)
def register_customer_account(
    payload: CustomerAccountRegisterRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CustomerAccountView:
    try:
        account, session_token = customer_account_service.register(
            email=payload.email,
            password=payload.password,
            project_id=payload.project_id,
            customer_token=payload.customer_token,
        )
    except (
        CustomerAccountConflictError,
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
    ) as exc:
        raise _account_error(exc) from exc
    _set_session_cookie(response, session_token, settings)
    self_dogfood_service.record_project_event_best_effort(
        payload.project_id,
        event_type="SIGNUP",
        business_key=f"account:{account.account_id}",
        settings=settings,
    )
    return account


@router.post("/customer/account/login", response_model=CustomerAccountView)
def login_customer_account(
    payload: CustomerAccountLoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CustomerAccountView:
    try:
        account, session_token = customer_account_service.login(
            email=payload.email,
            password=payload.password,
        )
    except CustomerAccountAuthenticationError as exc:
        raise _account_error(exc) from exc
    _set_session_cookie(response, session_token, settings)
    return account


@router.post("/customer/account/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_customer_account(
    response: Response,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> Response:
    customer_account_service.logout(session_token)
    response.delete_cookie(CUSTOMER_ACCOUNT_SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/customer/account/me", response_model=CustomerAccountView)
def current_customer_account(
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAccountView:
    try:
        return customer_account_service.view_for_session(session_token)
    except CustomerAccountAuthenticationError as exc:
        raise _account_error(exc) from exc


@router.post("/customer/account/projects/claim", response_model=CustomerAccountView)
def claim_customer_project(
    payload: CustomerAccountClaimProjectRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAccountView:
    try:
        return customer_account_service.claim_project(
            session_token=session_token,
            project_id=payload.project_id,
            customer_token=payload.customer_token,
        )
    except (
        CustomerAccountAuthenticationError,
        CustomerAccountConflictError,
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
    ) as exc:
        raise _account_error(exc) from exc


@router.get("/customer/workspace/{project_id}", response_model=CustomerWorkspaceView)
def get_customer_workspace(
    project_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerWorkspaceView:
    account, customer_token = _project_access(session_token, project_id)
    try:
        project_payload = customer_funnel_service.get_project_payload(project_id, customer_token)
        project = customer_funnel_service.get_project(project_id, customer_token)
        autopilot = customer_autopilot_service.overview(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, ValueError) as exc:
        raise _account_error(exc) from exc
    target_max_cac_raw = project_payload.get("autopilot_target_max_cac")
    self_dogfood_service.record_project_event_best_effort(
        project_id,
        event_type="ACTIVATED",
        business_key="first-authenticated-workspace",
        settings=settings,
    )
    return CustomerWorkspaceView(
        account=account,
        project=project,
        autopilot=autopilot,
        target_max_cac=float(target_max_cac_raw) if target_max_cac_raw is not None else None,
        autonomous_spend_confirmed=bool(project_payload.get("autopilot_spend_confirmed")),
    )


@router.put(
    "/customer/workspace/{project_id}/autopilot",
    response_model=CustomerAutopilotOverview,
)
def configure_customer_workspace_autopilot(
    project_id: UUID,
    payload: CustomerAutopilotConfigureRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAutopilotOverview:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return customer_autopilot_service.configure(project_id, customer_token, payload)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, ValueError, RuntimeError) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/autopilot/status",
    response_model=CustomerAutopilotOverview,
)
def set_customer_workspace_autopilot_status(
    project_id: UUID,
    payload: CustomerAutopilotStatusRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAutopilotOverview:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return customer_autopilot_service.set_status(project_id, customer_token, payload.status)
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
        CustomerPaymentRequiredError,
        ValueError,
    ) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/growth-balance/checkout",
    response_model=CheckoutResponse,
)
def create_workspace_growth_balance_checkout(
    project_id: UUID,
    payload: CustomerGrowthBalanceTopUpRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CheckoutResponse:
    _, customer_token = _project_access(session_token, project_id)
    try:
        generation, stripe_customer_id, amount_cents = growth_balance_service.prepare_checkout(
            project_id,
            customer_token,
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
            return_path="/workspace",
        )
        growth_balance_service.mark_checkout_pending(
            project_id,
            customer_token,
            session_id=checkout.session_id,
            amount_cents=amount_cents,
        )
        return CheckoutResponse(checkout_url=checkout.url)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError, ValueError) as exc:
        raise _account_error(exc) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Growth Balance checkout is unavailable") from exc


@router.post(
    "/customer/workspace/{project_id}/growth-balance/verify",
    response_model=CustomerAutopilotOverview,
)
def verify_workspace_growth_balance_checkout(
    project_id: UUID,
    payload: CustomerGrowthBalanceVerifyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAutopilotOverview:
    _, customer_token = _project_access(session_token, project_id)
    try:
        pending = growth_balance_service.pending(payload.session_id)
        if pending is None:
            raise HTTPException(status_code=401, detail="Growth Balance Checkout Session is not pending")
        session = retrieve_launch_checkout(settings=settings, session_id=payload.session_id)
        metadata = session.get("metadata") or {}
        amount_total = int(session.get("amount_total") or 0)
        currency = str(session.get("currency") or "").lower()
        verified = (
            str(session.get("id") or "") == payload.session_id
            and str(session.get("client_reference_id") or "") == str(project_id)
            and str(metadata.get("partizan_project_id") or "") == str(project_id)
            and metadata.get("partizan_entitlement") == "growth_balance_topup"
            and int(metadata.get("partizan_amount_cents") or 0)
            == int(pending.get("amount_cents") or 0)
            and session.get("mode") == "payment"
            and session.get("payment_status") == "paid"
            and amount_total == int(pending.get("amount_cents") or 0)
            and currency == "usd"
        )
        if not verified:
            raise HTTPException(status_code=401, detail="Growth Balance payment could not be verified")
        credited = growth_balance_service.credit_paid_checkout(
            project_id,
            session_id=payload.session_id,
            amount_cents=amount_total,
            currency=currency,
            stripe_customer_id=(str(session["customer"]) if session.get("customer") else None),
        )
        if not credited:
            raise HTTPException(
                status_code=401,
                detail="Growth Balance payment is not linked to this project",
            )
        return customer_autopilot_service.overview(project_id, customer_token)
    except HTTPException:
        raise
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Growth Balance verification failed") from exc


@router.post(
    "/customer/workspace/{project_id}/deep-research",
    response_model=CustomerResearchResponse,
)
async def start_workspace_deep_research(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerResearchResponse:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return await customer_funnel_service.start_deep_research(project_id, customer_token)
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
        CustomerPaymentRequiredError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/clarifications",
    response_model=CustomerResearchResponse,
)
async def answer_workspace_clarification(
    project_id: UUID,
    payload: CustomerClarificationAnswerRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerResearchResponse:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return await customer_funnel_service.answer_clarification(
            project_id,
            customer_token,
            payload,
        )
    except (
        CustomerProjectNotFoundError,
        CustomerProjectAccessError,
        CustomerPaymentRequiredError,
        KeyError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/meta/connect",
    response_model=CustomerMetaConnectResponse,
)
def begin_workspace_meta_oauth(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerMetaConnectResponse:
    _, customer_token = _project_access(session_token, project_id)
    try:
        url = customer_meta_oauth_service.begin(
            project_id,
            customer_token,
            return_path="/workspace",
        )
        return CustomerMetaConnectResponse(authorization_url=url)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc
    except CustomerMetaOAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/meta/options",
    response_model=CustomerMetaOptionsView,
)
def get_workspace_meta_options(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerMetaOptionsView:
    _, customer_token = _project_access(session_token, project_id)
    try:
        return customer_meta_oauth_service.options(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc


@router.post(
    "/customer/workspace/{project_id}/meta/connection",
    response_model=CustomerAutopilotOverview,
)
def save_workspace_meta_connection(
    project_id: UUID,
    payload: CustomerMetaConnectionRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> CustomerAutopilotOverview:
    _, customer_token = _project_access(session_token, project_id)
    try:
        customer_meta_oauth_service.connect(project_id, customer_token, payload)
        return customer_autopilot_service.overview(project_id, customer_token)
    except (CustomerProjectNotFoundError, CustomerProjectAccessError) as exc:
        raise _account_error(exc) from exc
    except CustomerMetaOAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
