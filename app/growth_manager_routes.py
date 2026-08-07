from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.growth_manager_schemas import (
    DecisionHistoryView,
    GrowthDecisionView,
    LearningMemoryView,
    ProductDecisionHistoryView,
)
from app.growth_manager_service import growth_manager_service

router = APIRouter(prefix="/v1", tags=["growth-manager"])


@router.post(
    "/experiments/{experiment_id}/decision",
    response_model=GrowthDecisionView,
)
async def evaluate_experiment(experiment_id: UUID) -> GrowthDecisionView:
    try:
        return growth_manager_service.evaluate(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment, product or play not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/experiments/{experiment_id}/decisions",
    response_model=DecisionHistoryView,
)
async def get_experiment_decisions(experiment_id: UUID) -> DecisionHistoryView:
    try:
        return growth_manager_service.experiment_history(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc


@router.get(
    "/products/{product_id}/decisions",
    response_model=ProductDecisionHistoryView,
)
async def get_product_decisions(product_id: UUID) -> ProductDecisionHistoryView:
    try:
        return growth_manager_service.product_history(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc


@router.get(
    "/products/{product_id}/learning-memory",
    response_model=LearningMemoryView,
)
async def get_learning_memory(product_id: UUID) -> LearningMemoryView:
    try:
        return growth_manager_service.learning_memory(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
