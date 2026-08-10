from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.distribution_execution_schemas import (
    DistributionActionEditRequest,
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
    DistributionExecutionPrepareRequest,
    DistributionExperimentView,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.product_intake import product_intake_service

router = APIRouter(tags=["distribution-execution"])


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
        return distribution_execution_service.prepare(product, play, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product or DistributionPlay not found",
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


@router.patch(
    "/distribution-actions/{action_id}",
    response_model=DistributionExecutionPlanView,
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
)
async def approve_distribution_action(action_id: UUID) -> DistributionExecutionPlanView:
    try:
        return distribution_execution_service.approve(action_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="DistributionAction dependency not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/skip",
    response_model=DistributionExecutionPlanView,
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
)
async def mark_distribution_action_executed(
    action_id: UUID,
    payload: DistributionActionExecutionRequest,
) -> DistributionExecutionPlanView:
    try:
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
