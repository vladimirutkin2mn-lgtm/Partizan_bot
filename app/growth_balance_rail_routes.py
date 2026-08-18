from __future__ import annotations

from typing import Annotated
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.customer_funnel import CustomerProjectNotFoundError
from app.growth_balance import growth_balance_service

router = APIRouter(prefix="/v1", tags=["growth-balance"])


class MetaBillingBindingRequest(BaseModel):
    ad_account_id: str = Field(min_length=1, max_length=120)
    confirm_partizan_card_primary: bool
    confirm_customer_payment_method_not_used: bool


def _webhook_secret(secret, *, label: str) -> str:
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{label} webhook is not configured",
        )
    return secret.get_secret_value()


def _construct_event(
    *,
    payload: bytes,
    signature: str | None,
    secret: str,
):
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe-Signature header required",
        )
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook signature",
        ) from exc


@router.get("/customer-projects/{project_id}/growth-balance/rail")
def get_growth_balance_rail(project_id: UUID) -> dict:
    """Operator-only, non-sensitive view of a customer settlement rail."""

    return growth_balance_service.rail_view(project_id)


@router.post("/customer-projects/{project_id}/growth-balance/rail/meta-binding")
def confirm_growth_balance_meta_binding(
    project_id: UUID,
    payload: MetaBillingBindingRequest,
) -> dict:
    """Confirm that the Partizan card is the active billing rail for this Meta account.

    PAN/CVC are intentionally not accepted by this API. The operator performs the
    provider-side billing attachment through the approved provider UI/process and then
    records only the non-sensitive binding fact here.
    """

    if not payload.confirm_partizan_card_primary:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirm that the Partizan-funded card is the active Meta billing method",
        )
    if not payload.confirm_customer_payment_method_not_used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirm that customer-owned billing is not used for Partizan spend",
        )
    try:
        growth_balance_service.confirm_meta_binding(project_id, payload.ad_account_id)
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return growth_balance_service.rail_view(project_id)


@router.post("/billing/stripe/issuing-authorizations")
async def stripe_issuing_authorization_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> JSONResponse:
    """Synchronous Stripe Issuing authorization decision.

    The card-level MCC/amount controls remain the first safety boundary. This webhook
    adds current Partizan state: active subscription, active Growth Mandate, provider
    binding and remaining Growth Balance acquisition capacity.
    """

    secret = _webhook_secret(
        settings.stripe_issuing_authorization_webhook_secret,
        label="Stripe Issuing authorization",
    )
    event = _construct_event(
        payload=await request.body(),
        signature=stripe_signature,
        secret=secret,
    )
    event_type = str(event.get("type") or "")
    approved = False
    if event_type == "issuing_authorization.request":
        approved = growth_balance_service.authorize_request(event["data"]["object"])
    return JSONResponse(
        status_code=200,
        content={"approved": approved},
        headers={"Stripe-Version": settings.stripe_issuing_webhook_api_version},
    )


@router.post("/billing/stripe/issuing-events")
async def stripe_issuing_events_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, bool]:
    """Persist financial Issuing captures/refunds as Growth Balance source of truth."""

    secret = _webhook_secret(
        settings.stripe_issuing_events_webhook_secret,
        label="Stripe Issuing events",
    )
    event = _construct_event(
        payload=await request.body(),
        signature=stripe_signature,
        secret=secret,
    )
    event_type = str(event.get("type") or "")
    if event_type in {"issuing_transaction.created", "issuing_transaction.updated"}:
        try:
            growth_balance_service.record_issuing_transaction(event["data"]["object"])
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"received": True}
