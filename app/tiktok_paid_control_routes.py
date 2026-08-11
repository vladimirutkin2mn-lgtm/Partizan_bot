from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.operator_auth import require_operator
from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditResult,
    PaidLifecycleState,
    paid_audit_ledger,
    paid_lifecycle_service,
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
        before = paid_lifecycle_service.get(action_id)
        snapshot = tiktok_paid_control_service.sync(action_id)
        after = paid_lifecycle_service.get(action_id)
        paid_audit_ledger.record(
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
        if before.state != PaidLifecycleState.PAUSED and after.state == PaidLifecycleState.PAUSED:
            paid_audit_ledger.record(
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
        before = paid_lifecycle_service.get(action_id)
        snapshot = tiktok_paid_control_service.pause(action_id)
        after = paid_lifecycle_service.get(action_id)
        paid_audit_ledger.record(
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
