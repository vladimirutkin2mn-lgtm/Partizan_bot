from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.analytics_schemas import (
    AnalyticsEventCreate,
    AnalyticsEventReceipt,
    ExperimentAnalyticsView,
    ProductAnalyticsView,
    SpendCreate,
    SpendReceipt,
)
from app.analytics_service import analytics_service
from app.product_intake import product_intake_service

router = APIRouter(prefix="/v1", tags=["analytics"])


@router.post(
    "/analytics/events",
    response_model=AnalyticsEventReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_analytics_event(payload: AnalyticsEventCreate) -> AnalyticsEventReceipt:
    try:
        return analytics_service.ingest_event(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Attribution target not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/experiments/{experiment_id}/spend",
    response_model=SpendReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_experiment_spend(experiment_id: UUID, payload: SpendCreate) -> SpendReceipt:
    try:
        return analytics_service.add_spend(experiment_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/experiments/{experiment_id}/analytics",
    response_model=ExperimentAnalyticsView,
)
async def get_experiment_analytics(experiment_id: UUID) -> ExperimentAnalyticsView:
    try:
        return analytics_service.experiment_analytics(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc


@router.get(
    "/products/{product_id}/analytics",
    response_model=ProductAnalyticsView,
)
async def get_product_analytics(product_id: UUID) -> ProductAnalyticsView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return analytics_service.product_analytics(product_id)
