from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_play_schemas import DistributionPlayGenerationResponse
from app.distribution_play_service import distribution_play_service
from app.product_intake import product_intake_service

router = APIRouter(prefix="/v1", tags=["distribution-plays"])


@router.post(
    "/products/{product_id}/distribution-plays/generate",
    response_model=DistributionPlayGenerationResponse,
)
async def generate_distribution_plays(product_id: UUID) -> DistributionPlayGenerationResponse:
    try:
        product = product_intake_service.get_product(product_id)
        distribution_map = audience_intelligence_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Run Audience Intelligence distribution discovery before planning tactics",
        ) from exc

    try:
        return distribution_play_service.generate(product, distribution_map)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/distribution-plays",
    response_model=DistributionPlayGenerationResponse,
)
async def get_distribution_plays(product_id: UUID) -> DistributionPlayGenerationResponse:
    try:
        return distribution_play_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution play generation not found") from exc
