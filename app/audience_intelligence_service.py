from uuid import UUID

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_schemas import AudienceDistributionMapView
from app.schemas import ICPGenerationResponse, ProductProfileView
from app.search import get_search_provider


class InMemoryAudienceIntelligenceService:
    def __init__(self) -> None:
        self._results: dict[UUID, AudienceDistributionMapView] = {}

    async def discover(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        top_icp_count: int = 3,
    ) -> AudienceDistributionMapView:
        top_icps = icp_result.icps[:top_icp_count]
        if not top_icps:
            raise ValueError("ICP generation must contain at least one segment")

        engine = AudienceIntelligenceEngine(get_search_provider())
        opportunities = await engine.discover(product=product, icps=top_icps)
        if not opportunities:
            raise RuntimeError("Audience Intelligence produced no MVP distribution opportunities")

        response = AudienceDistributionMapView(
            product_id=product.id,
            top_icp_count=len(top_icps),
            opportunity_count=len(opportunities),
            opportunities=opportunities,
        )
        self._results[product.id] = response
        return response

    def get(self, product_id: UUID) -> AudienceDistributionMapView:
        return self._results[product_id]

    def reset(self) -> None:
        self._results.clear()


audience_intelligence_service = InMemoryAudienceIntelligenceService()
