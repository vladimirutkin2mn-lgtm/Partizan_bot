from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.distribution_analytics_schemas import (
    DistributionAnalyticsEventCreate,
    DistributionAnalyticsEventReceipt,
    DistributionExperimentAnalyticsView,
    DistributionGrowthDecisionView,
    DistributionLearningMemoryView,
    DistributionPortfolioView,
    DistributionProductAnalyticsView,
    DistributionSpendCreate,
    DistributionSpendReceipt,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_schemas import DistributionExperimentView
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.operator_auth import require_operator

router = APIRouter(tags=["distribution-learning"])


@router.post(
    "/distribution-analytics/events",
    response_model=DistributionAnalyticsEventReceipt,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
async def ingest_distribution_event(
    payload: DistributionAnalyticsEventCreate,
) -> DistributionAnalyticsEventReceipt:
    try:
        return distribution_analytics_service.ingest_event(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-experiments/{experiment_id}/spend",
    response_model=DistributionSpendReceipt,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
async def add_distribution_spend(
    experiment_id: UUID,
    payload: DistributionSpendCreate,
) -> DistributionSpendReceipt:
    try:
        return distribution_analytics_service.add_spend(experiment_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-experiments/{experiment_id}/finish",
    response_model=DistributionExperimentView,
    dependencies=[Depends(require_operator)],
)
async def finish_distribution_experiment(experiment_id: UUID) -> DistributionExperimentView:
    try:
        return distribution_execution_service.finish_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-experiments/{experiment_id}/analytics",
    response_model=DistributionExperimentAnalyticsView,
)
async def get_distribution_experiment_analytics(
    experiment_id: UUID,
) -> DistributionExperimentAnalyticsView:
    try:
        return distribution_analytics_service.experiment_analytics(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionExperiment not found") from exc


@router.get(
    "/products/{product_id}/distribution-analytics",
    response_model=DistributionProductAnalyticsView,
)
async def get_distribution_product_analytics(
    product_id: UUID,
) -> DistributionProductAnalyticsView:
    return distribution_analytics_service.product_analytics(product_id)


@router.post(
    "/distribution-experiments/{experiment_id}/growth-decision",
    response_model=DistributionGrowthDecisionView,
)
async def evaluate_distribution_experiment(
    experiment_id: UUID,
) -> DistributionGrowthDecisionView:
    try:
        return distribution_growth_manager_service.evaluate(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution experiment context not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/distribution-learning",
    response_model=DistributionLearningMemoryView,
)
async def get_distribution_learning_memory(
    product_id: UUID,
) -> DistributionLearningMemoryView:
    try:
        return distribution_growth_manager_service.learning_memory(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc


@router.get(
    "/products/{product_id}/distribution-portfolio",
    response_model=DistributionPortfolioView,
)
async def get_distribution_portfolio(
    product_id: UUID,
    max_items: int = Query(default=4, ge=1, le=12),
) -> DistributionPortfolioView:
    try:
        return distribution_growth_manager_service.portfolio(
            product_id,
            max_items=max_items,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generate Distribution Plays before requesting a portfolio",
        ) from exc
