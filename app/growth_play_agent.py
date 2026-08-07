from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.llm import LLMMessage, LLMProvider
from app.schemas import ChannelDiscoveryResponse, ICPGenerationResponse, ProductProfileView


class PlayScoreDimensions(BaseModel):
    expected_impact: int = Field(ge=1, le=10)
    confidence: int = Field(ge=1, le=10)
    cost_efficiency: int = Field(ge=1, le=10)
    speed_to_signal: int = Field(ge=1, le=10)


class GrowthPlayDraft(BaseModel):
    channel_id: UUID
    template_id: str
    hypothesis: str = Field(min_length=20)
    offer: str = Field(min_length=3)
    execution_steps: list[str] = Field(min_length=3)
    success_metric: str = Field(min_length=3)
    expected_result: str = Field(min_length=3)
    kill_criteria: str = Field(min_length=3)
    scale_criteria: str = Field(min_length=3)
    estimated_cost_min: float = Field(ge=0)
    estimated_cost_max: float = Field(ge=0)
    effort_hours: float = Field(gt=0)
    time_to_signal_days: int = Field(ge=1, le=90)
    dimensions: PlayScoreDimensions
    rationale: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cost_range(self) -> "GrowthPlayDraft":
        if self.estimated_cost_max < self.estimated_cost_min:
            raise ValueError("estimated_cost_max must be >= estimated_cost_min")
        return self


class GrowthPlayGeneration(BaseModel):
    plays: list[GrowthPlayDraft] = Field(min_length=20, max_length=40)


@dataclass(frozen=True, slots=True)
class RankedGrowthPlay:
    draft: GrowthPlayDraft
    priority_score: float
    score_explanation: str


@dataclass(frozen=True, slots=True)
class GrowthPlayTemplate:
    template_id: str
    source_type: str
    tactic: str
    offer: str
    estimated_cost_min: float
    estimated_cost_max: float
    effort_hours: float
    time_to_signal_days: int
    dimensions: PlayScoreDimensions


PLAY_SCORE_WEIGHTS = {
    "expected_impact": 0.35,
    "confidence": 0.25,
    "cost_efficiency": 0.20,
    "speed_to_signal": 0.20,
}

PLAY_TEMPLATES = (
    GrowthPlayTemplate(
        template_id="community_value_post",
        source_type="community",
        tactic="value-first community contribution",
        offer="useful native content with a contextual product CTA when allowed",
        estimated_cost_min=0,
        estimated_cost_max=50,
        effort_hours=2,
        time_to_signal_days=3,
        dimensions=PlayScoreDimensions(
            expected_impact=6,
            confidence=6,
            cost_efficiency=10,
            speed_to_signal=8,
        ),
    ),
    GrowthPlayTemplate(
        template_id="community_partnership",
        source_type="community",
        tactic="community owner or moderator partnership",
        offer="exclusive benefit, AMA, useful resource or tracked member offer",
        estimated_cost_min=50,
        estimated_cost_max=300,
        effort_hours=3,
        time_to_signal_days=7,
        dimensions=PlayScoreDimensions(
            expected_impact=7,
            confidence=6,
            cost_efficiency=8,
            speed_to_signal=6,
        ),
    ),
    GrowthPlayTemplate(
        template_id="creator_seeding",
        source_type="creator",
        tactic="micro-creator product seeding",
        offer="free access plus a tracked referral link for an authentic review/use case",
        estimated_cost_min=50,
        estimated_cost_max=300,
        effort_hours=3,
        time_to_signal_days=7,
        dimensions=PlayScoreDimensions(
            expected_impact=8,
            confidence=7,
            cost_efficiency=8,
            speed_to_signal=7,
        ),
    ),
    GrowthPlayTemplate(
        template_id="creator_affiliate",
        source_type="creator",
        tactic="creator affiliate or revenue-share test",
        offer="tracked CPA or revenue share with creator-specific landing/message",
        estimated_cost_min=0,
        estimated_cost_max=100,
        effort_hours=3,
        time_to_signal_days=10,
        dimensions=PlayScoreDimensions(
            expected_impact=9,
            confidence=7,
            cost_efficiency=10,
            speed_to_signal=6,
        ),
    ),
    GrowthPlayTemplate(
        template_id="creator_sponsored_test",
        source_type="creator",
        tactic="small sponsored creator integration",
        offer="paid native integration with tracked CTA and fixed test budget",
        estimated_cost_min=200,
        estimated_cost_max=800,
        effort_hours=3,
        time_to_signal_days=7,
        dimensions=PlayScoreDimensions(
            expected_impact=8,
            confidence=6,
            cost_efficiency=6,
            speed_to_signal=8,
        ),
    ),
    GrowthPlayTemplate(
        template_id="newsletter_sponsorship",
        source_type="newsletter_site",
        tactic="niche newsletter sponsorship",
        offer="small tracked placement with a segment-specific CTA",
        estimated_cost_min=100,
        estimated_cost_max=500,
        effort_hours=2,
        time_to_signal_days=7,
        dimensions=PlayScoreDimensions(
            expected_impact=7,
            confidence=7,
            cost_efficiency=7,
            speed_to_signal=8,
        ),
    ),
    GrowthPlayTemplate(
        template_id="newsletter_affiliate",
        source_type="newsletter_site",
        tactic="newsletter affiliate or revenue-share partnership",
        offer="tracked CPA or revenue share with a dedicated reader offer",
        estimated_cost_min=0,
        estimated_cost_max=100,
        effort_hours=3,
        time_to_signal_days=14,
        dimensions=PlayScoreDimensions(
            expected_impact=8,
            confidence=6,
            cost_efficiency=10,
            speed_to_signal=5,
        ),
    ),
    GrowthPlayTemplate(
        template_id="content_partnership",
        source_type="newsletter_site",
        tactic="editorial or useful-content partnership",
        offer="expert contribution, tool/resource or co-created content with tracked CTA",
        estimated_cost_min=0,
        estimated_cost_max=150,
        effort_hours=5,
        time_to_signal_days=21,
        dimensions=PlayScoreDimensions(
            expected_impact=7,
            confidence=6,
            cost_efficiency=9,
            speed_to_signal=4,
        ),
    ),
)

SYSTEM_PROMPT = """You are the Growth Play Agent for Partizan Bot.

Turn a confirmed product, ranked ICP hypotheses and evidence-backed ChannelOpportunity objects
into 20-30 concrete acquisition experiments.

Rules:
1. Every play must reference one supplied channel_id. Never invent a channel or contact detail.
2. Make the play executable: hypothesis, offer, at least 3 steps, success metric, expected result,
   kill criteria, scale criteria, estimated cost range, effort and time-to-signal.
3. Prefer low-cost and fast-learning experiments before expensive scaling.
4. Keep plays diverse across source types and ICPs; do not generate paraphrased duplicates.
5. Cost and impact are hypotheses, not facts. Do not claim guaranteed results.
6. Do not propose spam, fake accounts, fake reviews, fake engagement, impersonation, ban evasion,
   or bypassing community/platform restrictions.
7. Community participation must be value-first and consistent with community rules.
8. Outbound messages, public posting and spend remain subject to user approval.
9. Score each play 1-10 on expected_impact, confidence, cost_efficiency and speed_to_signal.
10. The application, not you, computes final priority.

Return the requested structured schema only.
"""


class GrowthPlayGenerator:
    def __init__(self, provider: LLMProvider | None) -> None:
        self._provider = provider

    async def generate(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        channel_result: ChannelDiscoveryResponse,
    ) -> list[RankedGrowthPlay]:
        allowed_channels = {item.id: item for item in channel_result.opportunities}
        if self._provider is None:
            drafts = self._fallback_plays(product, icp_result, channel_result)
        else:
            generation = await self._provider.parse(
                messages=self._build_messages(product, icp_result, channel_result),
                response_model=GrowthPlayGeneration,
            )
            drafts = [play for play in generation.plays if play.channel_id in allowed_channels]
            if len(drafts) < 20:
                fallback = self._fallback_plays(product, icp_result, channel_result)
                existing = {(play.channel_id, play.template_id) for play in drafts}
                drafts.extend(
                    play
                    for play in fallback
                    if (play.channel_id, play.template_id) not in existing
                )
                drafts = drafts[:30]

        ranked = [
            RankedGrowthPlay(
                draft=draft,
                priority_score=self.calculate_priority(draft.dimensions),
                score_explanation=self.explain_priority(draft.dimensions),
            )
            for draft in drafts
        ]
        ranked.sort(key=lambda item: (-item.priority_score, str(item.draft.channel_id)))
        return ranked

    def calculate_priority(self, dimensions: PlayScoreDimensions) -> float:
        values = dimensions.model_dump()
        weighted = sum(values[name] * weight for name, weight in PLAY_SCORE_WEIGHTS.items())
        return round(weighted * 10, 1)

    def explain_priority(self, dimensions: PlayScoreDimensions) -> str:
        values = dimensions.model_dump()
        strongest = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:2]
        weakest = min(values.items(), key=lambda item: (item[1], item[0]))
        strong_text = ", ".join(f"{name}={score}/10" for name, score in strongest)
        return (
            f"Главные драйверы: {strong_text}. "
            f"Главное ограничение: {weakest[0]}={weakest[1]}/10."
        )

    def _build_messages(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        channel_result: ChannelDiscoveryResponse,
    ) -> list[LLMMessage]:
        icp_map = {str(icp.id): icp for icp in icp_result.icps}
        channels: list[dict[str, Any]] = []
        for channel in channel_result.opportunities[:36]:
            icp = icp_map.get(str(channel.icp_id))
            channels.append(
                {
                    "channel_id": str(channel.id),
                    "icp_id": str(channel.icp_id),
                    "icp_title": icp.title if icp else None,
                    "source_type": channel.source_type,
                    "platform": channel.platform,
                    "title": channel.title,
                    "url": channel.url,
                    "relevance_score": channel.relevance_score,
                    "rationale": channel.rationale,
                }
            )
        product_summary = {
            "name": product.name,
            "problem_or_desire": product.problem_or_desire,
            "value_proposition": product.value_proposition,
            "usp": product.usp,
            "market": product.market,
            "price": product.price,
            "pricing_model": product.pricing_model,
            "goal": product.goal,
            "budget": product.budget,
            "max_cac": product.max_cac,
            "constraints": product.constraints,
        }
        return [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=(
                    f"Product: {product_summary}\n\n"
                    f"Available ChannelOpportunity objects: {channels}"
                ),
            ),
        ]

    def _fallback_plays(
        self,
        product: ProductProfileView,
        icp_result: ICPGenerationResponse,
        channel_result: ChannelDiscoveryResponse,
    ) -> list[GrowthPlayDraft]:
        icp_map = {icp.id: icp for icp in icp_result.icps}
        templates_by_source: dict[str, list[GrowthPlayTemplate]] = {}
        for template in PLAY_TEMPLATES:
            templates_by_source.setdefault(template.source_type, []).append(template)

        plays: list[GrowthPlayDraft] = []
        source_counters: dict[str, int] = {}
        for channel in channel_result.opportunities:
            templates = templates_by_source.get(channel.source_type, [])
            if not templates:
                continue
            position = source_counters.get(channel.source_type, 0)
            template = templates[position % len(templates)]
            source_counters[channel.source_type] = position + 1
            icp = icp_map.get(channel.icp_id)
            icp_title = icp.title if icp else "target ICP"
            cost_min, cost_max = self._cap_cost(
                template.estimated_cost_min,
                template.estimated_cost_max,
                product.budget,
            )
            plays.append(
                GrowthPlayDraft(
                    channel_id=channel.id,
                    template_id=template.template_id,
                    hypothesis=(
                        f"If we run {template.tactic} through {channel.title} for {icp_title}, "
                        "we can acquire measurable qualified traffic at a testable CAC."
                    ),
                    offer=template.offer,
                    execution_steps=[
                        f"Review {channel.url} and confirm placement/participation rules.",
                        f"Prepare a message and creative tailored to {icp_title}.",
                        "Create a tracked CTA/UTM or referral link and launch only after approval.",
                        "Measure visits, signups, paid users and observed CAC versus the guardrail.",
                    ],
                    success_metric=product.goal or "paid users and CAC",
                    expected_result=(
                        "Produce enough attributed acquisition data to decide whether this "
                        "channel/tactic combination deserves another iteration."
                    ),
                    kill_criteria=(
                        "Stop after the test budget or signal window if there is no qualified "
                        "conversion or CAC is materially above the target."
                    ),
                    scale_criteria=(
                        "Scale only after repeatable conversions at or below the target CAC "
                        "with no material quality deterioration."
                    ),
                    estimated_cost_min=cost_min,
                    estimated_cost_max=cost_max,
                    effort_hours=template.effort_hours,
                    time_to_signal_days=template.time_to_signal_days,
                    dimensions=template.dimensions,
                    rationale=[
                        f"Channel relevance score={channel.relevance_score}.",
                        f"Template chosen for source type={channel.source_type}.",
                    ],
                )
            )
            if len(plays) >= 24:
                break
        return plays

    def _cap_cost(
        self,
        cost_min: float,
        cost_max: float,
        budget: float | None,
    ) -> tuple[float, float]:
        if budget is None or budget <= 0:
            return cost_min, cost_max
        capped_max = min(cost_max, budget)
        capped_min = min(cost_min, capped_max)
        return capped_min, capped_max
