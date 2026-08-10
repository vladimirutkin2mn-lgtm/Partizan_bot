from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.audience_intelligence_service import audience_intelligence_service
from app.opportunity_enrichment import opportunity_enrichment_service
from app.opportunity_enrichment_schemas import (
    OpportunityEnrichmentView,
    ProductOpportunityEnrichmentView,
)
from app.product_intake import product_intake_service

router = APIRouter(tags=["distribution-enrichment"])


@router.post(
    "/products/{product_id}/distribution/enrich",
    response_model=ProductOpportunityEnrichmentView,
)
async def enrich_product_distribution(
    product_id: UUID,
    max_opportunities: int = 20,
) -> ProductOpportunityEnrichmentView:
    if not 1 <= max_opportunities <= 50:
        raise HTTPException(status_code=422, detail="max_opportunities must be between 1 and 50")
    try:
        product = product_intake_service.get_product(product_id)
        distribution_map = audience_intelligence_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product or Audience Distribution Map not found",
        ) from exc
    return await opportunity_enrichment_service.enrich_product(
        product,
        distribution_map.opportunities,
        max_opportunities=max_opportunities,
    )


@router.post(
    "/products/{product_id}/distribution-opportunities/{opportunity_id}/enrich",
    response_model=OpportunityEnrichmentView,
)
async def enrich_distribution_opportunity(
    product_id: UUID,
    opportunity_id: UUID,
) -> OpportunityEnrichmentView:
    try:
        product = product_intake_service.get_product(product_id)
        distribution_map = audience_intelligence_service.get(product_id)
        opportunity = audience_intelligence_service.find_opportunity(opportunity_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Product or Distribution Opportunity not found",
        ) from exc
    if not any(item.id == opportunity_id for item in distribution_map.opportunities):
        raise HTTPException(
            status_code=409,
            detail="Distribution Opportunity does not belong to this product",
        )
    return await opportunity_enrichment_service.enrich(product, opportunity)
