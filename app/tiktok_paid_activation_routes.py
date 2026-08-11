from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.execution_adapters import AdapterExecutionOutcome, DistributionAdapterExecutionView
from app.operator_auth import require_operator
from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditResult,
    PaidLifecycleNextAction,
    PaidLifecycleState,
    paid_audit_ledger,
    paid_lifecycle_service,
)
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
        before = paid_lifecycle_service.get(action_id)
        authorization = tiktok_paid_activation_service.authorize(action_id, payload)
        after = paid_lifecycle_service.get(action_id)
        paid_audit_ledger.record(
            action_id=action_id,
            event_type=PaidAuditEventType.ACTIVATION_AUTHORIZED,
            actor=PaidAuditActor.OPERATOR,
            result=PaidAuditResult.SUCCESS,
            before=before,
            after=after,
            correlation_id=authorization.id,
        )
        return authorization
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
        before = paid_lifecycle_service.get(action_id)
        result = tiktok_paid_activation_service.activate(action_id, payload)
        after = paid_lifecycle_service.get(action_id)
        attempted = before.model_copy(
            update={
                "state": PaidLifecycleState.ACTIVATION_ATTEMPTED,
                "safe_next_action": PaidLifecycleNextAction.RECONCILE,
                "observed_at": datetime.now(UTC),
            }
        )
        paid_audit_ledger.record(
            action_id=action_id,
            event_type=PaidAuditEventType.ACTIVATION_ATTEMPTED,
            actor=PaidAuditActor.OPERATOR,
            result=PaidAuditResult.UNKNOWN,
            before=before,
            after=attempted,
            correlation_id=payload.authorization_id,
        )
        succeeded = result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
        paid_audit_ledger.record(
            action_id=action_id,
            event_type=(
                PaidAuditEventType.ACTIVATION_SUCCEEDED
                if succeeded
                else PaidAuditEventType.ACTIVATION_FAILED
            ),
            actor=PaidAuditActor.OPERATOR,
            result=PaidAuditResult.SUCCESS if succeeded else PaidAuditResult.FAILED,
            before=attempted,
            after=after,
            correlation_id=payload.authorization_id,
            reason=None if succeeded else result.receipt.message,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok activation dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
