from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.action_drafting import (
    DistributionAutoPrepareRequest,
    distribution_action_drafting_service,
)
from app.distribution_execution_schemas import (
    DistributionActionEditRequest,
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
    DistributionExecutionPrepareRequest,
    DistributionExperimentView,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionAdapterExecutionView,
)
from app.operator_auth import require_operator
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)
from app.paid_audit_safe import append_paid_audit, observe_paid_lifecycle
from app.paid_campaign import PaidCampaignSpec, paid_campaign_spec_service
from app.paid_lifecycle_audit import PaidAuditActor, PaidAuditEventType, PaidAuditResult
from app.product_intake import product_intake_service

router = APIRouter(tags=["distribution-execution"])


def _ensure_paid_spec(plan: DistributionExecutionPlanView) -> None:
    if plan.action.action_type == DistributionActionType.PAID_CAMPAIGN:
        paid_campaign_spec_service.ensure(plan.action.id)


def _reject_generic_outreach_action(action_id: UUID, operation: str) -> None:
    action = distribution_execution_service.get_action(action_id)
    if action.action_type == DistributionActionType.OUTREACH_EMAIL:
        raise ValueError(
            f"Outreach email {operation} requires the dedicated outreach sender flow"
        )


def _reject_generic_outreach_play(action_type: DistributionActionType) -> None:
    if action_type == DistributionActionType.OUTREACH_EMAIL:
        raise ValueError(
            "Outreach email preparation requires the dedicated evidence-backed outreach flow"
        )


@router.post(
    "/products/{product_id}/distribution-plays/{play_id}/actions/prepare",
    response_model=DistributionExecutionPlanView,
)
async def prepare_distribution_action(
    product_id: UUID,
    play_id: UUID,
    payload: DistributionExecutionPrepareRequest,
) -> DistributionExecutionPlanView:
    try:
        product = product_intake_service.get_product(product_id)
        play = distribution_play_service.find(product_id, play_id)
        _reject_generic_outreach_play(play.action_type)
        plan = distribution_execution_service.prepare(product, play, payload)
        _ensure_paid_spec(plan)
        return plan
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product or DistributionPlay not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/products/{product_id}/distribution-plays/{play_id}/actions/auto-prepare",
    response_model=DistributionExecutionPlanView,
)
async def auto_prepare_distribution_action(
    product_id: UUID,
    play_id: UUID,
    payload: DistributionAutoPrepareRequest,
) -> DistributionExecutionPlanView:
    try:
        product = product_intake_service.get_product(product_id)
        play = distribution_play_service.find(product_id, play_id)
        _reject_generic_outreach_play(play.action_type)
        plan = await distribution_action_drafting_service.auto_prepare(
            product=product,
            play=play,
            destination_url=payload.destination_url,
        )
        _ensure_paid_spec(plan)
        return plan
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product, DistributionPlay or dependency not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}",
    response_model=DistributionExecutionPlanView,
)
async def get_distribution_action(action_id: UUID) -> DistributionExecutionPlanView:
    try:
        return distribution_execution_service.get_plan(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction not found") from exc


@router.get(
    "/distribution-actions/{action_id}/paid-campaign-spec",
    response_model=PaidCampaignSpec,
)
async def get_paid_campaign_spec(action_id: UUID) -> PaidCampaignSpec:
    try:
        action = distribution_execution_service.get_action(action_id)
        if action.action_type != DistributionActionType.PAID_CAMPAIGN:
            raise ValueError("DistributionAction is not a paid campaign")
        spec = paid_campaign_spec_service.get(action_id)
        if spec is None:
            raise KeyError(action_id)
        return spec
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Paid campaign spec not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/distribution-actions/{action_id}",
    response_model=DistributionExecutionPlanView,
    dependencies=[Depends(require_operator)],
)
async def edit_distribution_action(
    action_id: UUID,
    payload: DistributionActionEditRequest,
) -> DistributionExecutionPlanView:
    try:
        return distribution_execution_service.edit(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/approve",
    response_model=DistributionExecutionPlanView,
    dependencies=[Depends(require_operator)],
)
async def approve_distribution_action(action_id: UUID) -> DistributionExecutionPlanView:
    try:
        _reject_generic_outreach_action(action_id, "approval")
        return distribution_execution_service.approve(action_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="DistributionAction dependency not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/execute",
    response_model=DistributionAdapterExecutionView,
    dependencies=[Depends(require_operator)],
)
async def execute_distribution_action(
    action_id: UUID,
    payload: DistributionAdapterExecuteRequest,
) -> DistributionAdapterExecutionView:
    try:
        _reject_generic_outreach_action(action_id, "execution")
        action = distribution_execution_service.get_action(action_id)
        audited_paid = (
            action.action_type == DistributionActionType.PAID_CAMPAIGN
            and action.platform in {DistributionPlatform.INSTAGRAM, DistributionPlatform.TIKTOK}
        )
        before = observe_paid_lifecycle(action_id) if audited_paid else None
        result = organic_creative_distribution_execution_adapter_service.execute(
            action_id,
            payload,
        )
        if audited_paid:
            after = observe_paid_lifecycle(action_id)
            outcome = result.receipt.outcome
            audit_result = (
                PaidAuditResult.SUCCESS
                if outcome in {AdapterExecutionOutcome.STAGED, AdapterExecutionOutcome.EXECUTED}
                else PaidAuditResult.SKIPPED
                if outcome == AdapterExecutionOutcome.UNAVAILABLE
                else PaidAuditResult.FAILED
            )
            append_paid_audit(
                action_id=action_id,
                event_type=PaidAuditEventType.PROVIDER_STAGING,
                actor=PaidAuditActor.OPERATOR,
                result=audit_result,
                before=before,
                after=after,
                correlation_id=result.receipt.external_reference,
                reason=result.receipt.message if audit_result != PaidAuditResult.SUCCESS else None,
                deduplicate=True,
            )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/skip",
    response_model=DistributionExecutionPlanView,
    dependencies=[Depends(require_operator)],
)
async def skip_distribution_action(action_id: UUID) -> DistributionExecutionPlanView:
    try:
        return distribution_execution_service.skip(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/mark-executed",
    response_model=DistributionExecutionPlanView,
    dependencies=[Depends(require_operator)],
)
async def mark_distribution_action_executed(
    action_id: UUID,
    payload: DistributionActionExecutionRequest,
) -> DistributionExecutionPlanView:
    try:
        _reject_generic_outreach_action(action_id, "completion")
        return distribution_execution_service.mark_executed(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-experiments/{experiment_id}",
    response_model=DistributionExperimentView,
)
async def get_distribution_experiment(experiment_id: UUID) -> DistributionExperimentView:
    try:
        return distribution_execution_service.get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc
