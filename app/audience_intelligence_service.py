from uuid import UUID, uuid4

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_schemas import (
    AudienceDistributionMapView,
    DistributionOpportunityView,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ICPGenerationResponse, ProductProfileView
from app.search import get_search_provider

AUDIENCE_MAP_NAMESPACE = "audience_distribution_map"
AUDIENCE_OPPORTUNITY_NAMESPACE = "audience_distribution_opportunity"


class InMemoryAudienceIntelligenceService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._results: dict[UUID, AudienceDistributionMapView] = {}
        self._opportunities: dict[UUID, DistributionOpportunityView] = {}

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
        seeds = await engine.discover(product=product, icps=top_icps)
        if not seeds:
            raise RuntimeError("Audience Intelligence produced no MVP distribution opportunities")

        opportunities = [
            DistributionOpportunityView(id=uuid4(), **seed.model_dump())
            for seed in seeds
        ]
        response = AudienceDistributionMapView(
            product_id=product.id,
            top_icp_count=len(top_icps),
            opportunity_count=len(opportunities),
            opportunities=opportunities,
        )
        self._results[product.id] = response
        self._store.put(
            AUDIENCE_MAP_NAMESPACE,
            str(product.id),
            response.model_dump(mode="json"),
        )
        for opportunity in opportunities:
            self._opportunities[opportunity.id] = opportunity
            self._store.put(
                AUDIENCE_OPPORTUNITY_NAMESPACE,
                str(opportunity.id),
                opportunity.model_dump(mode="json"),
            )
        return response

    def get(self, product_id: UUID) -> AudienceDistributionMapView:
        cached = self._results.get(product_id)
        if cached is not None:
            return cached
        payload = self._store.get(AUDIENCE_MAP_NAMESPACE, str(product_id))
        if payload is None:
            raise KeyError(product_id)
        result = AudienceDistributionMapView.model_validate(payload)
        self._results[product_id] = result
        for opportunity in result.opportunities:
            self._opportunities[opportunity.id] = opportunity
        return result

    def find_opportunity(self, opportunity_id: UUID) -> DistributionOpportunityView:
        cached = self._opportunities.get(opportunity_id)
        if cached is not None:
            return cached
        payload = self._store.get(
            AUDIENCE_OPPORTUNITY_NAMESPACE,
            str(opportunity_id),
        )
        if payload is None:
            raise KeyError(opportunity_id)
        opportunity = DistributionOpportunityView.model_validate(payload)
        self._opportunities[opportunity_id] = opportunity
        return opportunity

    def reset(self) -> None:
        self._results.clear()
        self._opportunities.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(AUDIENCE_MAP_NAMESPACE)
            self._store.clear_namespace(AUDIENCE_OPPORTUNITY_NAMESPACE)


audience_intelligence_service = InMemoryAudienceIntelligenceService()
