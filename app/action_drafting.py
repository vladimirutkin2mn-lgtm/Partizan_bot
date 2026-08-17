from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, HttpUrl

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import (
    DistributionExecutionPlanView,
    DistributionExecutionPrepareRequest,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_planner import TACTIC_CATALOG
from app.distribution_play_schemas import DistributionPlayStatus, DistributionPlayView
from app.distribution_schemas import (
    CommunityPolicyView,
    DistributionOpportunityView,
)
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.llm import LLMMessage, LLMProvider, get_llm_provider
from app.marketing_intelligence import marketing_task_for_action, render_marketing_guidance
from app.schemas import ProductProfileView


class DistributionAutoPrepareRequest(BaseModel):
    destination_url: HttpUrl | None = None


class DistributionContentDraft(BaseModel):
    context_text: str = Field(min_length=10, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    rationale: str = Field(min_length=5, max_length=2000)


@dataclass(frozen=True, slots=True)
class SelectedActionTarget:
    url: str | None
    context_text: str
    source: str


class ActionTargetSelector:
    def select(
        self,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> SelectedActionTarget:
        if play.action_type in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            candidates = self._candidate_targets(opportunity)
            if not candidates:
                raise ValueError(
                    "No concrete public ActionTarget is available; enrich the opportunity first"
                )
            candidate = candidates[0]
            context = self._target_context(candidate, opportunity)
            if len(context.strip()) < 10:
                raise ValueError(
                    "ActionTarget has insufficient local context; enrich the opportunity before drafting"
                )
            return SelectedActionTarget(
                url=str(candidate["url"]),
                context_text=context,
                source=str(candidate.get("source") or "enrichment"),
            )

        if play.action_type == DistributionActionType.STANDALONE_POST:
            if opportunity.url is None:
                raise ValueError("Standalone post requires a concrete community Opportunity URL")
            return SelectedActionTarget(
                url=str(opportunity.url),
                context_text=self._opportunity_context(opportunity),
                source="opportunity",
            )

        return SelectedActionTarget(
            url=None,
            context_text=self._opportunity_context(opportunity),
            source="opportunity",
        )

    def _candidate_targets(self, opportunity: DistributionOpportunityView) -> list[dict]:
        enrichment = opportunity.metadata.get("enrichment", {})
        raw_targets = enrichment.get("action_targets", [])
        candidates: list[dict] = []
        seen: set[str] = set()
        for raw in raw_targets:
            if not isinstance(raw, dict) or not raw.get("url"):
                continue
            url = str(raw["url"])
            if not self._is_action_target(opportunity.platform, url) or url in seen:
                continue
            candidates.append({**raw, "source": "enrichment.action_targets"})
            seen.add(url)
        for evidence in opportunity.evidence:
            if not isinstance(evidence, dict) or not evidence.get("url"):
                continue
            url = str(evidence["url"])
            if not self._is_action_target(opportunity.platform, url) or url in seen:
                continue
            candidates.append(
                {
                    "url": url,
                    "title": evidence.get("title"),
                    "snippet": evidence.get("snippet"),
                    "source": "opportunity.evidence",
                }
            )
            seen.add(url)
        return candidates

    def _is_action_target(self, platform: DistributionPlatform, url: str) -> bool:
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return False
        host = parts.netloc.lower().removeprefix("www.")
        segments = [segment for segment in parts.path.split("/") if segment]
        lowered = [segment.lower() for segment in segments]

        if platform == DistributionPlatform.TELEGRAM:
            if host not in {"t.me", "telegram.me"}:
                return False
            if segments and segments[0] == "s":
                segments = segments[1:]
            return len(segments) >= 2 and segments[-1].isdigit()
        if platform == DistributionPlatform.INSTAGRAM:
            return (
                (host == "instagram.com" or host.endswith(".instagram.com"))
                and bool(lowered)
                and lowered[0] in {"p", "reel", "reels"}
            )
        if platform == DistributionPlatform.REDDIT:
            return (
                (host == "reddit.com" or host.endswith(".reddit.com"))
                and "r" in lowered
                and "comments" in lowered
            )
        if platform == DistributionPlatform.TIKTOK:
            return (
                (host == "tiktok.com" or host.endswith(".tiktok.com"))
                and "video" in lowered
            )
        return False

    def _target_context(
        self,
        candidate: dict,
        opportunity: DistributionOpportunityView,
    ) -> str:
        title = str(candidate.get("title") or "").strip()
        snippet = str(candidate.get("snippet") or "").strip()
        parts = [part for part in (title, snippet) if part]
        if parts:
            return "\n".join(parts)
        return self._opportunity_context(opportunity)

    def _opportunity_context(self, opportunity: DistributionOpportunityView) -> str:
        evidence = [
            str(item.get("snippet") or "").strip()
            for item in opportunity.evidence[:3]
            if isinstance(item, dict) and item.get("snippet")
        ]
        parts = [
            f"Opportunity: {opportunity.title}",
            f"Platform: {opportunity.platform.value}",
            str(opportunity.rationale or "").strip(),
            *evidence,
        ]
        return "\n".join(part for part in parts if part)[:8000]


ACTION_DRAFT_SYSTEM_PROMPT = """You are the Distribution Action Drafting component for Partizan.
Prepare one concise, relevant draft for an already selected marketing experiment.

Non-negotiable rules:
1. Never impersonate a satisfied customer, independent reviewer, journalist, moderator, or unrelated person.
2. Never invent personal experience, testimonials, results, popularity, urgency, scarcity, or guarantees.
3. Use the supplied local target context. Do not write a generic mass-spam message.
4. Do not write cold direct messages. The allowed action type is supplied explicitly.
5. Community comments/replies should be useful in their own right.
   Do not include a product link unless it is explicitly allowed.
6. If an applied community policy requires disclosure, include a clear short disclosure.
7. Do not claim a platform rule permits something unless the applied CommunityPolicy explicitly says so.
8. Keep the draft compatible with an approval-gated assisted/manual execution flow.
Return only the requested structured schema.
"""


class DistributionActionComposer:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    async def compose(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
        target: SelectedActionTarget,
        policy: CommunityPolicyView | None,
    ) -> DistributionContentDraft:
        if self._provider is None:
            return self._mock_draft(
                product=product,
                play=play,
                opportunity=opportunity,
                target=target,
                policy=policy,
            )
        template = next(item for item in TACTIC_CATALOG if item.tactic_id == play.tactic_id)
        marketing_task = marketing_task_for_action(
            play.action_type.value,
            opportunity.platform.value,
        )
        marketing_guidance = render_marketing_guidance(marketing_task)
        return await self._provider.parse(
            messages=[
                LLMMessage(
                    role="system",
                    content=f"{ACTION_DRAFT_SYSTEM_PROMPT}\n\n{marketing_guidance}",
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"Product: {product.model_dump(mode='json')}\n"
                        f"Distribution play: {play.model_dump(mode='json')}\n"
                        f"Opportunity: {opportunity.model_dump(mode='json')}\n"
                        f"Selected target: {target}\n"
                        f"Applied CommunityPolicy: "
                        f"{policy.model_dump(mode='json') if policy else None}\n"
                        f"Tactic allows direct product link: {template.has_direct_product_link}\n"
                        f"Tactic includes product mention: {template.has_product_mention}\n"
                    ),
                ),
            ],
            response_model=DistributionContentDraft,
        )

    def _mock_draft(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
        target: SelectedActionTarget,
        policy: CommunityPolicyView | None,
    ) -> DistributionContentDraft:
        context = target.context_text[:8000]
        disclosure = ""
        if policy is not None and policy.disclosure_required:
            disclosure = "Disclosure: this is a Partizan-operated account testing relevant tools. "

        if play.action_type in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            content = (
                f"{disclosure}One useful angle here is to separate the immediate question from the "
                "underlying decision you are trying to make. Writing down the evidence for each "
                "interpretation can make the next step much clearer."
            )
            rationale = "Value-first response grounded in the selected public discussion context."
        elif play.action_type == DistributionActionType.STANDALONE_POST:
            product_reference = product.value_proposition or product.description
            content = (
                f"{disclosure}A practical framework for {opportunity.title}: start with the exact "
                "question, list the assumptions behind it, then compare two or three possible "
                f"interpretations. One tool we are evaluating is {product.name}: {product_reference}"
            )
            rationale = "Educational standalone draft with transparent product context."
        elif play.action_type == DistributionActionType.ORGANIC_VIDEO:
            content = (
                "Hook: A question worth asking before you act.\n"
                f"Body: Frame the problem around {opportunity.title}, show two competing "
                "interpretations, and end with one concrete reflection prompt.\n"
                "CTA: Explore the current tool in the profile if it is relevant to you."
            )
            rationale = "Short-form creative brief for a Partizan-owned thematic identity."
        elif play.action_type == DistributionActionType.PAID_CAMPAIGN:
            product_reference = product.value_proposition or product.description
            content = (
                f"Paid test brief for {product.name}: lead with the user problem, communicate "
                f"the value proposition ({product_reference}), use one clear CTA, and measure "
                "activation and paid conversion against the configured CAC guardrail."
            )
            rationale = "Approval-ready campaign brief; no platform launch is performed."
        else:
            raise ValueError(f"Unsupported action type for auto drafting: {play.action_type.value}")

        return DistributionContentDraft(
            context_text=context,
            content_text=content[:12000],
            rationale=rationale,
        )


class DistributionActionDraftingService:
    def __init__(
        self,
        selector: ActionTargetSelector | None = None,
        composer: DistributionActionComposer | None = None,
    ) -> None:
        self._selector = selector or ActionTargetSelector()
        self._composer = composer or DistributionActionComposer(get_llm_provider())

    async def auto_prepare(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        destination_url: HttpUrl | None = None,
    ) -> DistributionExecutionPlanView:
        if play.status != DistributionPlayStatus.READY:
            raise ValueError("Only READY DistributionPlay objects can be auto-prepared")
        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        target = self._selector.select(play, opportunity)
        policy = self._applied_policy(play, opportunity)
        draft = await self._composer.compose(
            product=product,
            play=play,
            opportunity=opportunity,
            target=target,
            policy=policy,
        )
        return distribution_execution_service.prepare(
            product,
            play,
            DistributionExecutionPrepareRequest(
                destination_url=destination_url,
                target_url=target.url,
                context_text=draft.context_text,
                content_text=draft.content_text,
            ),
        )

    def _applied_policy(
        self,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> CommunityPolicyView | None:
        if not play.community_policy_required:
            return None
        try:
            return distribution_control_plane_service.get_policy(opportunity.id)
        except KeyError as exc:
            raise ValueError(
                "An explicitly applied CommunityPolicy is required before drafting this Reddit action"
            ) from exc


distribution_action_drafting_service = DistributionActionDraftingService()