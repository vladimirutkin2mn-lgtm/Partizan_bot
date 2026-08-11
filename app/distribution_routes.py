from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_routes import router as control_plane_router
from app.distribution_execution_routes import router as execution_router
from app.distribution_learning_routes import router as learning_router
from app.distribution_schemas import AudienceDistributionMapView
from app.icp_service import icp_service
from app.meta_paid_control_routes import router as meta_paid_control_router
from app.opportunity_enrichment_routes import router as enrichment_router
from app.paid_activation_routes import router as paid_activation_router
from app.paid_control_ops_routes import router as paid_control_ops_router
from app.paid_provider_routes import router as paid_provider_router
from app.product_intake import product_intake_service
from app.tiktok_paid_activation_routes import router as tiktok_paid_activation_router
from app.tiktok_paid_control_routes import router as tiktok_paid_control_router

router = APIRouter(prefix="/v1", tags=["distribution"])
router.include_router(control_plane_router)
router.include_router(execution_router)
router.include_router(learning_router)
router.include_router(enrichment_router)
router.include_router(paid_provider_router)
router.include_router(paid_activation_router)
router.include_router(meta_paid_control_router)
router.include_router(tiktok_paid_activation_router)
router.include_router(tiktok_paid_control_router)
router.include_router(paid_control_ops_router)


@router.post(
    "/products/{product_id}/distribution/discover",
    response_model=AudienceDistributionMapView,
)
async def discover_distribution(product_id: UUID) -> AudienceDistributionMapView:
    try:
        product = product_intake_service.get_product(product_id)
        icp_result = icp_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generate ICPs before distribution discovery",
        ) from exc
    try:
        return await audience_intelligence_service.discover(product, icp_result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/distribution",
    response_model=AudienceDistributionMapView,
)
async def get_distribution(product_id: UUID) -> AudienceDistributionMapView:
    try:
        return audience_intelligence_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution discovery not found") from exc
