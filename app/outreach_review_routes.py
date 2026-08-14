from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.outreach_briefs import OutreachBriefView
from app.outreach_review import (
    OutreachBriefEditRequest,
    outreach_brief_review_service,
)

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.patch(
    "/outreach-briefs/{brief_id}/review",
    response_model=OutreachBriefView,
)
async def edit_outreach_brief(
    brief_id: UUID,
    payload: OutreachBriefEditRequest,
) -> OutreachBriefView:
    try:
        return outreach_brief_review_service.edit(brief_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachBrief dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/outreach-briefs/{brief_id}/reject",
    response_model=OutreachBriefView,
)
async def reject_outreach_brief(brief_id: UUID) -> OutreachBriefView:
    try:
        return outreach_brief_review_service.reject(brief_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="OutreachBrief dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
