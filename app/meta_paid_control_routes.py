from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.meta_paid_control import MetaPaidControlSnapshotView, meta_paid_control_service
from app.operator_auth import require_operator

router = APIRouter(
    tags=["paid-provider-control"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/meta/sync",
    response_model=MetaPaidControlSnapshotView,
)
async def sync_meta_paid_campaign(action_id: UUID) -> MetaPaidControlSnapshotView:
    try:
        return meta_paid_control_service.sync(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Meta paid resource not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/paid-campaign/meta/pause",
    response_model=MetaPaidControlSnapshotView,
)
async def pause_meta_paid_campaign(action_id: UUID) -> MetaPaidControlSnapshotView:
    try:
        return meta_paid_control_service.pause(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Meta paid resource not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/paid-campaign/meta/control",
    response_model=MetaPaidControlSnapshotView,
)
async def get_meta_paid_control(action_id: UUID) -> MetaPaidControlSnapshotView:
    snapshot = meta_paid_control_service.get(action_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Meta paid control snapshot not found")
    return snapshot
