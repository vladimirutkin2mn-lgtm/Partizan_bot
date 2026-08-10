from uuid import UUID

from app.distribution_play_planner import DistributionPlayPlanner
from app.distribution_play_schemas import (
    DistributionPlayGenerationResponse,
    DistributionPlayStatus,
    DistributionPlayView,
)
from app.distribution_schemas import (
    AudienceDistributionMapView,
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
)
from app.schemas import ProductProfileView


class InMemoryDistributionPlayService:
    def __init__(self) -> None:
        self._results: dict[UUID, DistributionPlayGenerationResponse] = {}

    def generate(
        self,
        product: ProductProfileView,
        distribution_map: AudienceDistributionMapView,
        *,
        identities: list[DistributionIdentityView] | None = None,
        community_policies: list[CommunityPolicyView] | None = None,
        campaign_slots: list[CampaignSlotView] | None = None,
    ) -> DistributionPlayGenerationResponse:
        plays = DistributionPlayPlanner().plan(
            product=product,
            distribution_map=distribution_map,
            identities=identities,
            community_policies=community_policies,
            campaign_slots=campaign_slots,
        )
        if not plays:
            raise RuntimeError("Distribution play planning produced no MVP tactics")

        ready_count = sum(play.status == DistributionPlayStatus.READY for play in plays)
        response = DistributionPlayGenerationResponse(
            product_id=product.id,
            play_count=len(plays),
            ready_count=ready_count,
            blocked_count=len(plays) - ready_count,
            plays=plays,
        )
        self._results[product.id] = response
        return response

    def get(self, product_id: UUID) -> DistributionPlayGenerationResponse:
        return self._results[product_id]

    def find(self, product_id: UUID, play_id: UUID) -> DistributionPlayView:
        result = self._results[product_id]
        play = next((item for item in result.plays if item.id == play_id), None)
        if play is None:
            raise KeyError(play_id)
        return play

    def reset(self) -> None:
        self._results.clear()


distribution_play_service = InMemoryDistributionPlayService()
