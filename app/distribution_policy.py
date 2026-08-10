import re
from dataclasses import dataclass
from uuid import UUID

from app.distribution_schemas import (
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
    DistributionOpportunitySeed,
)
from app.distribution_types import (
    CampaignSlotStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
    is_valid_action_type,
)


@dataclass(frozen=True, slots=True)
class ExecutionPolicyDecision:
    allowed: bool
    reasons: tuple[str, ...]
    disclosure_required: bool = False


class DistributionExecutionPolicy:
    def evaluate(
        self,
        opportunity: DistributionOpportunitySeed,
        action_type: DistributionActionType,
        *,
        identity: DistributionIdentityView | None = None,
        community_policy: CommunityPolicyView | None = None,
        has_direct_product_link: bool = False,
        has_product_mention: bool = False,
    ) -> ExecutionPolicyDecision:
        reasons: list[str] = []

        if not is_valid_action_type(opportunity.platform, action_type):
            reasons.append(
                f"{action_type.value} is not supported for {opportunity.platform.value}"
            )

        identity_required = action_type != DistributionActionType.PAID_CAMPAIGN
        if identity_required:
            if identity is None:
                reasons.append("A Partizan Distribution Identity is required")
            else:
                reasons.extend(self._identity_reasons(opportunity, action_type, identity))

        disclosure_required = False
        if opportunity.platform == DistributionPlatform.REDDIT:
            reddit_decision = self._evaluate_reddit(
                action_type,
                community_policy,
                has_direct_product_link=has_direct_product_link,
                has_product_mention=has_product_mention,
            )
            reasons.extend(reddit_decision.reasons)
            disclosure_required = reddit_decision.disclosure_required

        return ExecutionPolicyDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            disclosure_required=disclosure_required,
        )

    def _identity_reasons(
        self,
        opportunity: DistributionOpportunitySeed,
        action_type: DistributionActionType,
        identity: DistributionIdentityView,
    ) -> list[str]:
        reasons: list[str] = []
        if identity.platform != opportunity.platform:
            reasons.append("Distribution Identity platform does not match opportunity")
        if identity.status != DistributionIdentityStatus.ACTIVE:
            reasons.append("Distribution Identity is not active")

        allowed_kinds = {
            str(value).upper() for value in identity.eligibility.get("allowed_opportunity_kinds", [])
        }
        if allowed_kinds and opportunity.kind.value not in allowed_kinds:
            reasons.append("Distribution Identity is not eligible for this opportunity kind")

        allowed_actions = {
            str(value).upper() for value in identity.eligibility.get("allowed_actions", [])
        }
        if allowed_actions and action_type.value not in allowed_actions:
            reasons.append("Distribution Identity is not eligible for this action type")
        return reasons

    def _evaluate_reddit(
        self,
        action_type: DistributionActionType,
        policy: CommunityPolicyView | None,
        *,
        has_direct_product_link: bool,
        has_product_mention: bool,
    ) -> ExecutionPolicyDecision:
        if action_type == DistributionActionType.PAID_CAMPAIGN:
            return ExecutionPolicyDecision(allowed=True, reasons=())
        if policy is None:
            return ExecutionPolicyDecision(
                allowed=False,
                reasons=("Reddit CommunityPolicy is required before community execution",),
            )

        reasons: list[str] = []
        if not policy.commercial_participation_allowed:
            reasons.append("Community policy does not allow commercial participation")

        if action_type in {DistributionActionType.COMMENT, DistributionActionType.REPLY}:
            if not policy.comments_allowed:
                reasons.append("Community policy does not allow comments/replies")
        elif action_type == DistributionActionType.STANDALONE_POST:
            if not policy.standalone_posts_allowed:
                reasons.append("Community policy does not allow standalone posts")

        if has_direct_product_link and not policy.links_allowed:
            reasons.append("Community policy does not allow direct links")
        if has_product_mention and not policy.product_mentions_allowed:
            reasons.append("Community policy does not allow product mentions")

        return ExecutionPolicyDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            disclosure_required=policy.disclosure_required,
        )


@dataclass(frozen=True, slots=True)
class IdentitySelection:
    identity: DistributionIdentityView
    score: float
    reasons: tuple[str, ...]


class DistributionIdentitySelector:
    def select(
        self,
        *,
        product_id: UUID,
        opportunity: DistributionOpportunitySeed,
        identities: list[DistributionIdentityView],
        campaign_slots: list[CampaignSlotView] | None = None,
        desired_language: str | None = None,
    ) -> IdentitySelection | None:
        campaign_slots = campaign_slots or []
        selections: list[IdentitySelection] = []

        for identity in identities:
            eligibility_reasons = self._eligibility_reasons(
                product_id,
                opportunity,
                identity,
                campaign_slots,
            )
            if eligibility_reasons:
                continue

            score, reasons = self._score(
                opportunity,
                identity,
                desired_language=desired_language,
            )
            selections.append(
                IdentitySelection(
                    identity=identity,
                    score=score,
                    reasons=tuple(reasons),
                )
            )

        if not selections:
            return None
        return max(selections, key=lambda selection: (selection.score, str(selection.identity.id)))

    def _eligibility_reasons(
        self,
        product_id: UUID,
        opportunity: DistributionOpportunitySeed,
        identity: DistributionIdentityView,
        campaign_slots: list[CampaignSlotView],
    ) -> list[str]:
        if identity.platform != opportunity.platform:
            return ["platform mismatch"]
        if identity.status != DistributionIdentityStatus.ACTIVE:
            return ["identity inactive"]

        allowed_kinds = {
            str(value).upper() for value in identity.eligibility.get("allowed_opportunity_kinds", [])
        }
        if allowed_kinds and opportunity.kind.value not in allowed_kinds:
            return ["opportunity kind not eligible"]

        for slot in campaign_slots:
            if (
                slot.distribution_identity_id == identity.id
                and slot.status == CampaignSlotStatus.ACTIVE
                and slot.product_id != product_id
            ):
                return ["identity assigned to another active client campaign"]
        return []

    def _score(
        self,
        opportunity: DistributionOpportunitySeed,
        identity: DistributionIdentityView,
        *,
        desired_language: str | None,
    ) -> tuple[float, list[str]]:
        opportunity_tokens = self._tokens(
            " ".join(
                [
                    opportunity.title,
                    opportunity.rationale or "",
                    str(opportunity.metadata.get("topic", "")),
                ]
            )
        )
        identity_tokens = self._tokens(
            " ".join([identity.theme, identity.public_positioning])
        )
        overlap = opportunity_tokens & identity_tokens

        score = 50.0
        reasons = ["platform and eligibility match"]
        if overlap:
            topic_bonus = min(35.0, 7.0 * len(overlap))
            score += topic_bonus
            reasons.append(f"theme overlap: {', '.join(sorted(overlap)[:5])}")

        if desired_language and identity.language:
            if identity.language.lower() == desired_language.lower():
                score += 10.0
                reasons.append("language match")
            else:
                score -= 20.0
                reasons.append("language mismatch")

        if opportunity.kind == OpportunityKind.CONTENT_CLUSTER:
            score += 5.0
            reasons.append("identity supports reusable topic-cluster learning")

        return max(0.0, min(100.0, score)), reasons

    def _tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
            if len(token) > 2
        }
