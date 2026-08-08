from uuid import UUID, uuid4

from app.channel_hunter import ChannelHunter
from app.schemas import (
    ChannelDiscoveryResponse,
    ChannelEvidenceView,
    ChannelOpportunityView,
    ICPGenerationResponse,
    ProductProfileView,
)
from app.search import get_search_provider


class InMemoryChannelService:
    def __init__(self) -> None:
        self._results: dict[UUID, ChannelDiscoveryResponse] = {}

    async def discover(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        top_icp_count: int = 3,
    ) -> ChannelDiscoveryResponse:
        top_icps = icp_result.icps[:top_icp_count]
        if not top_icps:
            raise ValueError("ICP generation must contain at least one segment")

        hunter = ChannelHunter(get_search_provider())
        candidates = await hunter.discover(product=product, icps=top_icps)
        if len(candidates) < 30:
            raise RuntimeError(
                f"Channel discovery produced only {len(candidates)} unique opportunities; "
                "at least 30 are required"
            )

        opportunities = [
            ChannelOpportunityView(
                id=uuid4(),
                icp_id=item.icp_id,
                source_type=item.source_class.value,
                platform=item.platform,
                title=item.title,
                url=item.url,
                relevance_score=item.relevance_score,
                rationale=item.rationale,
                evidence=[
                    ChannelEvidenceView(
                        query=evidence.query,
                        title=evidence.title,
                        url=evidence.url,
                        snippet=evidence.snippet,
                    )
                    for evidence in item.evidence
                ],
            )
            for item in candidates
        ]
        response = ChannelDiscoveryResponse(
            product_id=product.id,
            top_icp_count=len(top_icps),
            opportunity_count=len(opportunities),
            opportunities=opportunities,
        )
        self._results[product.id] = response
        return response

    def get(self, product_id: UUID) -> ChannelDiscoveryResponse:
        return self._results[product_id]

    def reset(self) -> None:
        self._results.clear()


channel_service = InMemoryChannelService()
