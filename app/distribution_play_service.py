from uuid import UUID

from app.distribution_play_planner import DistributionPlayPlanner
from app.distribution_play_schemas import (
    DistributionPlayGenerationResponse,
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_schemas import (
    AudienceDistributionMapView,
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView

DISTRIBUTION_PLAY_NAMESPACE = "distribution_play_generation"


class InMemoryDistributionPlayService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
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
        planned = DistributionPlayPlanner().plan(
            product=product,
            distribution_map=distribution_map,
            identities=identities,
            community_policies=community_policies,
            campaign_slots=campaign_slots,
        )
        outreach = self._persisted_outreach_plays(product.id)
        plays = planned + [play for play in outreach if play.id not in {item.id for item in planned}]
        if not plays:
            raise RuntimeError("Distribution play planning produced no MVP tactics")

        response = self._response(product.id, plays)
        self._results[product.id] = response
        self._persist(response)
        return response

    def register(self, play: DistributionPlayView) -> DistributionPlayGenerationResponse:
        if play.tactic_class != DistributionTacticClass.OUTREACH:
            raise ValueError("Only OUTREACH plays may be registered outside the planner")
        try:
            current = self.get(play.product_id)
            plays = list(current.plays)
        except KeyError:
            plays = []
        if any(item.id == play.id for item in plays):
            return self._response(play.product_id, plays)
        plays.append(play)
        plays.sort(
            key=lambda item: (
                item.status != DistributionPlayStatus.READY,
                -item.priority_score,
                item.tactic_id,
                str(item.opportunity_id),
            )
        )
        response = self._response(play.product_id, plays)
        self._results[play.product_id] = response
        self._persist(response)
        return response

    def get(self, product_id: UUID) -> DistributionPlayGenerationResponse:
        cached = self._results.get(product_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_PLAY_NAMESPACE, str(product_id))
        if payload is None:
            raise KeyError(product_id)
        result = DistributionPlayGenerationResponse.model_validate(payload)
        self._results[product_id] = result
        return result

    def find(self, product_id: UUID, play_id: UUID) -> DistributionPlayView:
        result = self.get(product_id)
        play = next((item for item in result.plays if item.id == play_id), None)
        if play is None:
            raise KeyError(play_id)
        return play

    def reset(self) -> None:
        self._results.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_PLAY_NAMESPACE)

    def _persisted_outreach_plays(self, product_id: UUID) -> list[DistributionPlayView]:
        payload = self._store.get(DISTRIBUTION_PLAY_NAMESPACE, str(product_id))
        if payload is None:
            return []
        current = DistributionPlayGenerationResponse.model_validate(payload)
        return [
            play
            for play in current.plays
            if play.tactic_class == DistributionTacticClass.OUTREACH
        ]

    def _response(
        self,
        product_id: UUID,
        plays: list[DistributionPlayView],
    ) -> DistributionPlayGenerationResponse:
        ready_count = sum(play.status == DistributionPlayStatus.READY for play in plays)
        return DistributionPlayGenerationResponse(
            product_id=product_id,
            play_count=len(plays),
            ready_count=ready_count,
            blocked_count=len(plays) - ready_count,
            plays=plays,
        )

    def _persist(self, response: DistributionPlayGenerationResponse) -> None:
        self._store.put(
            DISTRIBUTION_PLAY_NAMESPACE,
            str(response.product_id),
            response.model_dump(mode="json"),
        )


distribution_play_service = InMemoryDistributionPlayService()
