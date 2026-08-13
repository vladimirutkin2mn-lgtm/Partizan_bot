from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.outreach_autosend import (
    OutreachAutoSendDelegationCreateRequest,
    OutreachAutoSendDelegationStatusRequest,
    OutreachAutoSendDelegationView,
    OutreachAutonomousSendView,
    outreach_autonomous_send_service,
    outreach_autosend_delegation_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/products/{product_id}/outreach-autosend/delegate",
    response_model=OutreachAutoSendDelegationView,
)
async def delegate_outreach_autosend(
    product_id: UUID,
    payload: OutreachAutoSendDelegationCreateRequest,
) -> OutreachAutoSendDelegationView:
    try:
        return outreach_autosend_delegation_service.delegate(product_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Current Outreach Policy and Growth Mandate are required",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/outreach-autosend",
    response_model=OutreachAutoSendDelegationView,
)
async def get_outreach_autosend(product_id: UUID) -> OutreachAutoSendDelegationView:
    try:
        return outreach_autosend_delegation_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Outreach auto-send delegation not found",
        ) from exc


@router.post(
    "/products/{product_id}/outreach-autosend/status",
    response_model=OutreachAutoSendDelegationView,
)
async def set_outreach_autosend_status(
    product_id: UUID,
    payload: OutreachAutoSendDelegationStatusRequest,
) -> OutreachAutoSendDelegationView:
    try:
        return outreach_autosend_delegation_service.set_status(product_id, payload.status)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Outreach auto-send delegation not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/products/{product_id}/outreach-autosend/run-next",
    response_model=OutreachAutonomousSendView | None,
)
async def run_next_outreach_autosend(
    product_id: UUID,
) -> OutreachAutonomousSendView | None:
    try:
        return await outreach_autonomous_send_service.run_next(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="Outreach dependency is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
