from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.execution_adapters import DistributionAdapterExecutionView
from app.operator_auth import require_operator
from app.tiktok_paid_activation import (
    TikTokPaidActivationAuthorizationRequest,
    TikTokPaidActivationAuthorizationView,
    TikTokPaidActivationRequest,
    tiktok_paid_activation_service,
)

router = APIRouter(
    tags=["tiktok-paid-activation"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/tiktok/activation-authorizations",
    response_model=TikTokPaidActivationAuthorizationView,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_tiktok_paid_campaign_activation(
    action_id: UUID,
    payload: TikTokPaidActivationAuthorizationRequest,
) -> TikTokPaidActivationAuthorizationView:
    try:
        return tiktok_paid_activation_service.authorize(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok activation dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/tiktok/activate",
    response_model=DistributionAdapterExecutionView,
)
async def activate_tiktok_paid_campaign(
    action_id: UUID,
    payload: TikTokPaidActivationRequest,
) -> DistributionAdapterExecutionView:
    try:
        return tiktok_paid_activation_service.activate(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok activation dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
