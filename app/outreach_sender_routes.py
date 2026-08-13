from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.operator_auth import require_operator
from app.outreach_sender import (
    OutreachSendAttemptView,
    OutreachSendAuthorizationCreateRequest,
    OutreachSendAuthorizationView,
    OutreachSenderReadinessView,
    outreach_sender_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.get(
    "/outreach/sender-readiness",
    response_model=OutreachSenderReadinessView,
)
async def get_outreach_sender_readiness() -> OutreachSenderReadinessView:
    return outreach_sender_service.readiness()


@router.post(
    "/outreach-briefs/{brief_id}/send-authorizations",
    response_model=OutreachSendAuthorizationView,
    status_code=status.HTTP_201_CREATED,
)
async def create_outreach_send_authorization(
    brief_id: UUID,
    payload: OutreachSendAuthorizationCreateRequest,
) -> OutreachSendAuthorizationView:
    try:
        return outreach_sender_service.authorize(brief_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="OutreachBrief or dependency not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/outreach-send-authorizations/{authorization_id}",
    response_model=OutreachSendAuthorizationView,
)
async def get_outreach_send_authorization(
    authorization_id: UUID,
) -> OutreachSendAuthorizationView:
    try:
        return outreach_sender_service.get_authorization(authorization_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Outreach send authorization not found") from exc


@router.post(
    "/outreach-send-authorizations/{authorization_id}/send",
    response_model=OutreachSendAttemptView,
)
async def send_authorized_outreach_email(
    authorization_id: UUID,
) -> OutreachSendAttemptView:
    try:
        return await outreach_sender_service.send(authorization_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Outreach send authorization or dependency not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/outreach-briefs/{brief_id}/send-attempt",
    response_model=OutreachSendAttemptView,
)
async def get_outreach_send_attempt(brief_id: UUID) -> OutreachSendAttemptView:
    attempt = outreach_sender_service.get_attempt(brief_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Outreach send attempt not found")
    return attempt
