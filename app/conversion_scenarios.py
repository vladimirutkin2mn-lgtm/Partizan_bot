from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.distribution_play_planner import DistributionTacticTemplate
from app.distribution_play_schemas import DistributionPlayView
from app.distribution_schemas import CommunityPolicyView, DistributionOpportunityView
from app.distribution_types import DistributionActionType


class ConversionMechanism(StrEnum):
    DIRECT_LINK = "DIRECT_LINK"
    PROFILE_CLICK = "PROFILE_CLICK"
    BRAND_SEARCH = "BRAND_SEARCH"
    REPLY_ENGAGEMENT = "REPLY_ENGAGEMENT"


@dataclass(frozen=True, slots=True)
class DraftVariantBrief:
    conversion_mechanism: ConversionMechanism
    variant_name: str
    objective: str
    expected_user_next_step: str
    instructions: str


class ConversionScenarioPlanner:
    """Choose measurable conversion paths before copy is drafted."""

    def variants(
        self,
        *,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
        policy: CommunityPolicyView | None,
        template: DistributionTacticTemplate,
    ) -> tuple[DraftVariantBrief, ...]:
        del opportunity
        variants: list[DraftVariantBrief] = []

        if self._direct_link_allowed(template, policy):
            variants.extend(
                (
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.DIRECT_LINK,
                        variant_name="resource_cta",
                        objective=(
                            "Create value in-context, then convert qualified readers through one "
                            "explicit destination link."
                        ),
                        expected_user_next_step="Click the supplied product destination.",
                        instructions=(
                            "Lead with a useful answer to the local context. Explain why the linked "
                            "resource is relevant and finish with one low-pressure CTA. Do not hide "
                            "the commercial relationship or invent proof."
                        ),
                    ),
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.DIRECT_LINK,
                        variant_name="problem_solution",
                        objective=(
                            "Connect the problem in the discussion to the product's factual value "
                            "proposition and earn a direct click."
                        ),
                        expected_user_next_step="Click the supplied product destination.",
                        instructions=(
                            "Make the problem-to-solution bridge explicit but concise. Include only "
                            "claims grounded in the product profile and use one direct CTA."
                        ),
                    ),
                )
            )

        if self._brand_search_allowed(template, policy):
            variants.extend(
                (
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.BRAND_SEARCH,
                        variant_name="transparent_mention",
                        objective=(
                            "Create enough relevant product awareness that interested readers can "
                            "look up the product themselves."
                        ),
                        expected_user_next_step="Search for the product or inspect the author identity.",
                        instructions=(
                            "Give a useful answer first, then include one factual, transparent product "
                            "mention. Do not include a link unless the direct-link scenario is allowed."
                        ),
                    ),
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.BRAND_SEARCH,
                        variant_name="category_bridge",
                        objective=(
                            "Associate the product with the exact job-to-be-done in the conversation "
                            "without forcing a click."
                        ),
                        expected_user_next_step="Remember or search for the product name.",
                        instructions=(
                            "Bridge from the local problem to the product category and mention the "
                            "product once. Avoid slogans, hype, urgency, or repeated naming."
                        ),
                    ),
                )
            )

        if play.action_type in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            variants.extend(
                (
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.PROFILE_CLICK,
                        variant_name="expertise_signal",
                        objective=(
                            "Earn profile visits by publishing a distinctive, useful insight that makes "
                            "the author's point of view worth exploring."
                        ),
                        expected_user_next_step="Open the author profile out of legitimate curiosity.",
                        instructions=(
                            "Do not mention the product, include a link, or ask readers to open the "
                            "profile. Give a specific, non-generic insight tied to the target context. "
                            "The profile carries the CTA, so the comment itself must earn curiosity."
                        ),
                    ),
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.PROFILE_CLICK,
                        variant_name="non_obvious_lens",
                        objective=(
                            "Earn profile visits through a relevant, non-obvious framing of the topic."
                        ),
                        expected_user_next_step="Open the author profile out of legitimate curiosity.",
                        instructions=(
                            "Offer a useful angle that is less obvious than generic advice, while staying "
                            "grounded in supplied context. No clickbait, product mention, profile CTA, "
                            "withholding of safety information, or invented experience."
                        ),
                    ),
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.REPLY_ENGAGEMENT,
                        variant_name="thoughtful_question",
                        objective=(
                            "Start a real conversation that increases qualified visibility and creates "
                            "a natural follow-up opportunity."
                        ),
                        expected_user_next_step="Reply to the contribution with a substantive answer.",
                        instructions=(
                            "Give useful context first, then ask exactly one specific open question "
                            "that a relevant participant may genuinely want to answer. No engagement bait."
                        ),
                    ),
                    DraftVariantBrief(
                        conversion_mechanism=ConversionMechanism.REPLY_ENGAGEMENT,
                        variant_name="tradeoff_prompt",
                        objective=(
                            "Invite discussion around a meaningful trade-off already present in the "
                            "local context."
                        ),
                        expected_user_next_step="Reply with a preference, experience, or perspective.",
                        instructions=(
                            "Frame one concrete trade-off and invite perspectives. The contribution "
                            "must remain useful even if nobody replies. Do not use generic 'what do you "
                            "think?' bait."
                        ),
                    ),
                )
            )

        if variants:
            return tuple(variants)

        return (
            DraftVariantBrief(
                conversion_mechanism=ConversionMechanism.REPLY_ENGAGEMENT,
                variant_name="value_first",
                objective="Create a useful contribution and measure qualified follow-on engagement.",
                expected_user_next_step="Engage with the contribution if it is relevant.",
                instructions=(
                    "Prioritize usefulness and relevance. Do not add a product link or product mention "
                    "unless the applied policy and tactic explicitly allow it."
                ),
            ),
        )

    @staticmethod
    def _direct_link_allowed(
        template: DistributionTacticTemplate,
        policy: CommunityPolicyView | None,
    ) -> bool:
        if not template.has_direct_product_link:
            return False
        if policy is None:
            return True
        return (
            policy.commercial_participation_allowed
            and policy.self_promotion_allowed
            and policy.links_allowed
        )

    @staticmethod
    def _brand_search_allowed(
        template: DistributionTacticTemplate,
        policy: CommunityPolicyView | None,
    ) -> bool:
        if not template.has_product_mention:
            return False
        if policy is None:
            return True
        return (
            policy.commercial_participation_allowed
            and policy.self_promotion_allowed
            and policy.product_mentions_allowed
        )


conversion_scenario_planner = ConversionScenarioPlanner()
