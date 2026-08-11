from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.tiktok_paid_control import (
    TikTokPaidControlSnapshotView,
    tiktok_paid_control_service,
)

router = APIRouter(
    tags=["tiktok-paid-control"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/tiktok/sync",
    response_model=TikTokPaidControlSnapshotView,
)
async def sync_tiktok_paid_campaign(action_id: UUID) -> TikTokPaidControlSnapshotView:
    try:
        return tiktok_paid_control_service.sync(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok control dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/tiktok/pause",
    response_model=TikTokPaidControlSnapshotView,
)
async def pause_tiktok_paid_campaign(action_id: UUID) -> TikTokPaidControlSnapshotView:
    try:
        return tiktok_paid_control_service.pause(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok control dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/paid-campaign/tiktok/control",
    response_model=TikTokPaidControlSnapshotView,
)
async def get_tiktok_paid_campaign_control(action_id: UUID) -> TikTokPaidControlSnapshotView:
    snapshot = tiktok_paid_control_service.get(action_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="TikTok paid control snapshot not found")
    return snapshot
