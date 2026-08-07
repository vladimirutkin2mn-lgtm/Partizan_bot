from uuid import UUID, uuid4

from app.growth_play_agent import GrowthPlayGenerator
from app.llm import get_llm_provider
from app.schemas import (
    ChannelDiscoveryResponse,
    GrowthPlayGenerationResponse,
    GrowthPlayView,
    ICPGenerationResponse,
    PlayScoreBreakdownView,
    ProductProfileView,
)


class InMemoryGrowthPlayService:
    def __init__(self) -> None:
        self._results: dict[UUID, GrowthPlayGenerationResponse] = {}
        self._product_by_play: dict[UUID, UUID] = {}

    async def generate(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        channel_result: ChannelDiscoveryResponse,
    ) -> GrowthPlayGenerationResponse:
        generator = GrowthPlayGenerator(get_llm_provider())
        ranked = await generator.generate(product, icp_result, channel_result)
        if len(ranked) < 20:
            raise RuntimeError(
                f"Growth Play generation produced only {len(ranked)} plays; at least 20 required"
            )

        channel_map = {channel.id: channel for channel in channel_result.opportunities}
        plays: list[GrowthPlayView] = []
        for rank, item in enumerate(ranked, start=1):
            channel = channel_map[item.draft.channel_id]
            play_id = uuid4()
            play = GrowthPlayView(
                id=play_id,
                product_id=product.id,
                rank=rank,
                icp_id=channel.icp_id,
                channel_id=channel.id,
                source_type=channel.source_type,
                channel_url=channel.url,
                template_id=item.draft.template_id,
                hypothesis=item.draft.hypothesis,
                offer=item.draft.offer,
                execution_steps=item.draft.execution_steps,
                success_metric=item.draft.success_metric,
                expected_result=item.draft.expected_result,
                kill_criteria=item.draft.kill_criteria,
                scale_criteria=item.draft.scale_criteria,
                estimated_cost_min=item.draft.estimated_cost_min,
                estimated_cost_max=item.draft.estimated_cost_max,
                effort_hours=item.draft.effort_hours,
                time_to_signal_days=item.draft.time_to_signal_days,
                priority_score=item.priority_score,
                score_breakdown=PlayScoreBreakdownView(
                    **item.draft.dimensions.model_dump()
                ),
                score_explanation=item.score_explanation,
                rationale=item.draft.rationale,
                status="PROPOSED",
            )
            plays.append(play)
            self._product_by_play[play_id] = product.id

        response = GrowthPlayGenerationResponse(
            product_id=product.id,
            play_count=len(plays),
            plays=plays,
        )
        self._results[product.id] = response
        return response

    def get(self, product_id: UUID) -> GrowthPlayGenerationResponse:
        return self._results[product_id]

    def set_status(self, product_id: UUID, play_id: UUID, status: str) -> GrowthPlayView:
        if self._product_by_play[play_id] != product_id:
            raise KeyError(play_id)
        result = self._results[product_id]
        updated_plays: list[GrowthPlayView] = []
        updated_play: GrowthPlayView | None = None
        for play in result.plays:
            if play.id == play_id:
                updated_play = play.model_copy(update={"status": status})
                updated_plays.append(updated_play)
            else:
                updated_plays.append(play)
        if updated_play is None:
            raise KeyError(play_id)
        self._results[product_id] = result.model_copy(update={"plays": updated_plays})
        return updated_play

    def reset(self) -> None:
        self._results.clear()
        self._product_by_play.clear()


growth_play_service = InMemoryGrowthPlayService()
