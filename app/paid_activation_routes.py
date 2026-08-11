from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.execution_adapters import DistributionAdapterExecutionView
from app.operator_auth import require_operator
from app.paid_activation import (
    PaidActivationAuthorizationRequest,
    PaidActivationAuthorizationView,
    PaidActivationRequest,
    paid_activation_service,
)

router = APIRouter(tags=["paid-activation"], dependencies=[Depends(require_operator)])


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/activation-authorizations",
    response_model=PaidActivationAuthorizationView,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_paid_campaign_activation(
    action_id: UUID,
    payload: PaidActivationAuthorizationRequest,
) -> PaidActivationAuthorizationView:
    try:
        return paid_activation_service.authorize(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid activation dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/activate",
    response_model=DistributionAdapterExecutionView,
)
async def activate_paid_campaign(
    action_id: UUID,
    payload: PaidActivationRequest,
) -> DistributionAdapterExecutionView:
    try:
        return paid_activation_service.activate(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid activation dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
