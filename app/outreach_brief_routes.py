from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.operator_auth import require_operator
from app.outreach_briefs import (
    OutreachBriefCreateRequest,
    OutreachBriefListView,
    OutreachBriefView,
    outreach_brief_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/outreach-targets/{target_id}/briefs",
    response_model=OutreachBriefView,
    status_code=status.HTTP_201_CREATED,
)
async def create_outreach_brief(
    target_id: UUID,
    payload: OutreachBriefCreateRequest,
) -> OutreachBriefView:
    try:
        return await outreach_brief_service.create(target_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachTarget or product not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get(
    "/outreach-targets/{target_id}/briefs",
    response_model=OutreachBriefListView,
)
async def list_outreach_briefs(target_id: UUID) -> OutreachBriefListView:
    try:
        return outreach_brief_service.list_target(target_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachTarget not found") from exc


@router.get(
    "/outreach-briefs/{brief_id}",
    response_model=OutreachBriefView,
)
async def get_outreach_brief(brief_id: UUID) -> OutreachBriefView:
    try:
        return outreach_brief_service.get(brief_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachBrief not found") from exc
