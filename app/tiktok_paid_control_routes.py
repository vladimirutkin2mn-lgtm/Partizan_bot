from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.paid_audit_safe import append_paid_audit, observe_paid_lifecycle
from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditResult,
    PaidLifecycleState,
)
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
        before = observe_paid_lifecycle(action_id)
        snapshot = tiktok_paid_control_service.sync(action_id)
        after = observe_paid_lifecycle(action_id)
        append_paid_audit(
            action_id=action_id,
            event_type=PaidAuditEventType.CONTROL_SYNC,
            actor=PaidAuditActor.OPERATOR,
            result=(
                PaidAuditResult.FAILED
                if snapshot.requires_reconciliation or snapshot.sync_state == "UNKNOWN"
                else PaidAuditResult.SUCCESS
            ),
            before=before,
            after=after,
            reason=snapshot.last_error,
            deduplicate=True,
        )
        if (
            before is not None
            and after is not None
            and before.state != PaidLifecycleState.PAUSED
            and after.state == PaidLifecycleState.PAUSED
        ):
            append_paid_audit(
                action_id=action_id,
                event_type=PaidAuditEventType.PROVIDER_PAUSE,
                actor=PaidAuditActor.OPERATOR,
                result=PaidAuditResult.SUCCESS,
                before=before,
                after=after,
                reason=snapshot.pause_reason,
                deduplicate=True,
            )
        return snapshot
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
        before = observe_paid_lifecycle(action_id)
        snapshot = tiktok_paid_control_service.pause(action_id)
        after = observe_paid_lifecycle(action_id)
        append_paid_audit(
            action_id=action_id,
            event_type=PaidAuditEventType.PROVIDER_PAUSE,
            actor=PaidAuditActor.OPERATOR,
            result=(
                PaidAuditResult.SUCCESS
                if snapshot.pause_state == "CONFIRMED"
                else PaidAuditResult.FAILED
            ),
            before=before,
            after=after,
            reason=snapshot.last_error or snapshot.pause_reason,
            deduplicate=True,
        )
        return snapshot
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
