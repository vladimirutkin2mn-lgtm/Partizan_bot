import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, HttpUrl

from app.audience_intelligence_service import audience_intelligence_service
from app.community_message_prompt import (
    COMMUNITY_MESSAGE_GUIDANCE,
    COMMUNITY_MESSAGE_PROMPT_VERSION,
)
from app.conversion_scenarios import (
    DraftVariantBrief,
    conversion_scenario_planner,
)
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
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.llm import LLMMessage, LLMProvider, get_llm_provider
from app.marketing_intelligence import marketing_task_for_action, render_marketing_guidance
from app.openai_usage import openai_usage_context
from app.project_marketing_memory import (
    ProjectMarketingMemoryService,
    project_marketing_memory_service,
)
from app.reddit_research import action_target_is_fresh
from app.schemas import ProductProfileView
from app.telegram_target_excerpt import compact_telegram_target
from app.telegram_message_precheck import precheck_telegram_target


class DistributionAutoPrepareRequest(BaseModel):
    destination_url: HttpUrl | None = None


class DistributionContentDraft(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    context_text: str = Field(min_length=10, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    rationale: str = Field(min_length=5, max_length=2000)
    disclosure_included: bool = False
    ai_disclosure_included: bool = False


@dataclass(frozen=True, slots=True)
class PreparedDraftVariant:
    brief: DraftVariantBrief
    plan: DistributionExecutionPlanView


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
            candidates = (
                self._telegram_latest_target(play, opportunity)
                if opportunity.platform == DistributionPlatform.TELEGRAM
                else self._candidate_targets(opportunity)
            )
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
            if opportunity.platform == DistributionPlatform.TELEGRAM:
                precheck = precheck_telegram_target(context)
                if not precheck.eligible:
                    raise ValueError(
                        f"Telegram ActionTarget precheck blocked: {precheck.reason}"
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

    def _telegram_latest_target(
        self,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> list[dict]:
        """Never substitute a stale enrichment/evidence hit for the native latest post."""
        metadata = opportunity.metadata if isinstance(opportunity.metadata, dict) else {}
        if str(metadata.get("native_research_status") or "").upper() != "VERIFIED":
            raise ValueError("Telegram ActionTarget requires verified native research")
        expected_mode = (
            "CHANNEL_COMMENT"
            if play.action_type == DistributionActionType.COMMENT
            else "GROUP_REPLY"
        )
        mode = str(metadata.get("action_target_mode") or "").upper()
        if mode and mode != expected_mode:
            raise ValueError("Telegram ActionTarget mode differs from the planned action")
        target_url = str(metadata.get("action_target_url") or "").strip()
        if not metadata.get("action_target_specific") or not self._is_action_target(
            DistributionPlatform.TELEGRAM, target_url
        ):
            raise ValueError("Telegram ActionTarget has no available latest message")

        recent = metadata.get("recent_context")
        if not isinstance(recent, list) or not recent:
            raise ValueError("Telegram ActionTarget latest message has not been observed")
        candidates = [
            item
            for item in recent
            if isinstance(item, dict)
            and self._telegram_message_id(item) is not None
        ]
        if not candidates:
            raise ValueError("Telegram ActionTarget latest message identity is unknown")
        latest = max(candidates, key=self._telegram_message_id)
        latest_url = str(latest.get("url") or "").strip()
        if (
            not latest_url
            or latest_url.rstrip("/").split("?", 1)[0]
            != target_url.rstrip("/").split("?", 1)[0]
        ):
            raise ValueError("Telegram ActionTarget is not the latest native message")
        # A verified audience alone cannot justify an unrelated canned reply.
        # If the newest post is media-only and we have no usable context,
        # do not silently fall back to the community title or older posts.
        if not str(latest.get("text") or "").strip():
            raise ValueError(
                "Telegram latest native post has no readable context for a relevant draft"
            )
        return [
            {
                "url": target_url,
                "title": opportunity.title,
                "snippet": str(latest.get("text") or ""),
                "source": "telegram_native.latest_message",
            }
        ]

    @staticmethod
    def _telegram_message_id(context: dict) -> int | None:
        raw = context.get("message_id")
        if raw is not None:
            try:
                message_id = int(raw)
                return message_id if message_id > 0 else None
            except (TypeError, ValueError):
                return None
        # Legacy saved snapshots have URLs but no explicit message_id.
        path = urlsplit(str(context.get("url") or "")).path
        tail = path.strip("/").rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() and int(tail) > 0 else None

    def _candidate_targets(self, opportunity: DistributionOpportunityView) -> list[dict]:
        candidates: list[dict] = []
        seen: set[str] = set()

        enrichment = opportunity.metadata.get("enrichment", {})
        raw_targets = enrichment.get("action_targets", [])
        for raw in raw_targets:
            if not isinstance(raw, dict) or not raw.get("url"):
                continue
            url = str(raw["url"])
            if not self._is_action_target(opportunity.platform, url) or url in seen:
                continue
            if (
                opportunity.platform == DistributionPlatform.REDDIT
                and not action_target_is_fresh(raw)
            ):
                continue
            candidates.append({**raw, "source": "enrichment.action_targets"})
            seen.add(url)

        if opportunity.platform == DistributionPlatform.REDDIT:
            return candidates

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
        # Telegram text must be the selected native message, without community
        # title or other older-post context that the model could misattribute.
        if candidate.get("source") == "telegram_native.latest_message":
            return snippet
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
   For a Telegram comment/reply, use ONLY the actual newest native message
   supplied as the selected target. Respond naturally to what THAT post says;
   do not blend in older group messages or manufacture a problem or question.
   Community audience fit is established separately from message context.
   Mention the product only when it genuinely contributes to that newest post,
   and otherwise keep the reply useful without promotion. No generic ad copy.
6. When a Conversion brief is supplied, optimize for that explicit conversion mechanism instead
   of writing a merely polite or generic contribution. The conversion path must still obey every
   community and platform constraint.
7. If an applied community policy requires disclosure, include a clear short disclosure.
   Set disclosure_included=true only when that disclosure is actually present.
8. If the policy requires AI-content disclosure, include it.
   Set ai_disclosure_included=true only when that disclosure is actually present.
9. For STANDALONE_POST, provide a concise title that is suitable for the target community.
10. Do not claim a platform rule permits something unless the applied CommunityPolicy explicitly says so.
11. Drafting is independent of publishing. An authorized execution service handles
    assisted/manual review or explicitly opted-in autonomous posting with separate
    permission, moderation, deduplication and platform safety gates.
12. If the product or target is adult-oriented, keep comments/replies non-explicit and professional.
    Do not echo graphic sexual language from the source context.
Return only the requested structured schema.
"""


class DistributionActionComposer:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        memory_service: ProjectMarketingMemoryService | None = None,
    ) -> None:
        self._provider = provider
        self._memory = memory_service or project_marketing_memory_service

    async def compose(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
        target: SelectedActionTarget,
        policy: CommunityPolicyView | None,
        variant: DraftVariantBrief | None = None,
    ) -> DistributionContentDraft:
        self._validate_ai_policy(policy)
        if self._provider is None:
            return self._mock_draft(
                product=product,
                play=play,
                opportunity=opportunity,
                target=target,
                policy=policy,
                variant=variant,
            )
        template = next(item for item in TACTIC_CATALOG if item.tactic_id == play.tactic_id)
        marketing_task = marketing_task_for_action(
            play.action_type.value,
            opportunity.platform.value,
        )
        marketing_guidance = render_marketing_guidance(marketing_task)
        memory = self._memory.prompt_context_for_product(
            product.id,
            platform=opportunity.platform.value,
            action_type=play.action_type.value,
        )
        community_prompt = (
            f"Prompt version: {COMMUNITY_MESSAGE_PROMPT_VERSION}\n"
            + COMMUNITY_MESSAGE_GUIDANCE
            if opportunity.platform == DistributionPlatform.TELEGRAM
            and play.action_type in {
                DistributionActionType.COMMENT, DistributionActionType.REPLY
            }
            else ""
        )
        if community_prompt:
            # The generic opportunity payload can include dozens of older
            # Telegram messages and full discovery history. Keep only product
            # facts, community identity and the exact selected native target.
            product_data = product.model_dump(mode="json")
            community_metadata = getattr(opportunity, "metadata", {}) or {}
            compacted = compact_telegram_target(target.context_text)
            user_context = json.dumps(
                {
                    "product": {
                        key: str(product_data.get(key) or "")[:limit]
                        for key, limit in (
                            ("name", 90),
                            ("description", 240),
                            ("problem_or_desire", 160),
                            ("value_proposition", 180),
                        )
                    },
                    "community_title": opportunity.title,
                    "community_about": str(community_metadata.get("about") or "")[:220],
                    "audience_fit_hint": str(
                        community_metadata.get("community_fit_reason") or ""
                    )[:140],
                    "platform": opportunity.platform.value,
                    "action_type": play.action_type.value,
                    "tactic": play.tactic_id,
                    "selected_native_target": {
                        "url": target.url,
                        "text": compacted.text,
                        "source": target.source,
                        "excerpt": {
                            "source_chars": compacted.source_chars,
                            "included_chars": compacted.included_chars,
                            "compacted": compacted.compacted,
                            "strategy": compacted.strategy,
                        },
                    },
                    "project_marketing_memory": (memory.rendered or "")[:700],
                    "applied_community_policy": (
                        policy.model_dump(mode="json") if policy else None
                    ),
                    "tactic_allows_product_link": template.has_direct_product_link,
                    "tactic_mentions_product": template.has_product_mention,
                    "conversion_brief": self._variant_prompt(variant) if variant else None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            user_context = (
                f"Product: {product.model_dump(mode='json')}\n"
                f"Project marketing memory: {memory.rendered or None}\n"
                f"Distribution play: {play.model_dump(mode='json')}\n"
                f"Opportunity: {opportunity.model_dump(mode='json')}\n"
                f"Selected target: {target}\n"
                f"Applied CommunityPolicy: "
                f"{policy.model_dump(mode='json') if policy else None}\n"
                f"Tactic allows direct product link: {template.has_direct_product_link}\n"
                f"Tactic includes product mention: {template.has_product_mention}\n"
                f"Conversion brief: "
                f"{self._variant_prompt(variant) if variant else None}\n"
            )
        with openai_usage_context("action_variant_draft" if variant else "action_auto_prepare", product.id):
            return await self._provider.parse(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            f"{ACTION_DRAFT_SYSTEM_PROMPT}\n\n{marketing_guidance}"
                            f"\n\n{community_prompt}"
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=user_context,
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
        variant: DraftVariantBrief | None = None,
    ) -> DistributionContentDraft:
        context = target.context_text[:8000]
        disclosure_required = bool(policy and policy.disclosure_required)
        constraints = self._constraints(policy)
        ai_disclosure_required = "AI_CONTENT_DISCLOSURE_REQUIRED" in constraints
        disclosures: list[str] = []
        if disclosure_required:
            disclosures.append(
                "Disclosure: this is a Partizan-operated account testing relevant tools."
            )
        if ai_disclosure_required:
            disclosures.append("AI disclosure: this contribution was prepared with AI assistance.")
        disclosure_prefix = " ".join(disclosures)
        if disclosure_prefix:
            disclosure_prefix += " "

        title = None
        if play.action_type in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            if variant is not None and variant.conversion_mechanism.value == "PROFILE_CLICK":
                content = (
                    f"{disclosure_prefix}A useful distinction here is between the visible rule and "
                    "the decision it helps a participant make. The strongest guidance usually makes "
                    "both explicit, so people can judge fit before they commit."
                )
                rationale = (
                    "Distinctive value-first contribution designed to earn legitimate profile curiosity."
                )
            elif variant is not None and variant.conversion_mechanism.value == "REPLY_ENGAGEMENT":
                content = (
                    f"{disclosure_prefix}A useful distinction here is between the visible rule and "
                    "the decision it helps a participant make. Which part tends to create the most "
                    "uncertainty for people before they decide whether to participate?"
                )
                rationale = (
                    "Value-first contribution with one substantive question to create a natural reply."
                )
            else:
                content = (
                    f"{disclosure_prefix}One useful angle here is to separate the immediate question "
                    "from the underlying decision you are trying to make. Writing down the evidence "
                    "for each interpretation can make the next step much clearer."
                )
                rationale = "Value-first response grounded in the selected public discussion context."
        elif play.action_type == DistributionActionType.STANDALONE_POST:
            title = f"A practical framework for {opportunity.title}"[:300]
            product_reference = product.value_proposition or product.description
            content = (
                f"{disclosure_prefix}A practical framework for {opportunity.title}: start with the "
                "exact question, list the assumptions behind it, then compare two or three possible "
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
            title=title,
            context_text=context,
            content_text=content[:12000],
            rationale=rationale,
            disclosure_included=disclosure_required,
            ai_disclosure_included=ai_disclosure_required,
        )

    @staticmethod
    def _variant_prompt(variant: DraftVariantBrief) -> dict[str, str]:
        return {
            "conversion_mechanism": variant.conversion_mechanism.value,
            "variant_name": variant.variant_name,
            "objective": variant.objective,
            "expected_user_next_step": variant.expected_user_next_step,
            "instructions": variant.instructions,
        }

    def _validate_ai_policy(self, policy: CommunityPolicyView | None) -> None:
        if "AI_CONTENT_PROHIBITED" in self._constraints(policy):
            raise ValueError("Community policy prohibits AI-generated content")

    @staticmethod
    def _constraints(policy: CommunityPolicyView | None) -> set[str]:
        if policy is None:
            return set()
        return {str(item).upper() for item in policy.ai_content_constraints}


class DistributionActionDraftingService:
    def __init__(
        self,
        selector: ActionTargetSelector | None = None,
        composer: DistributionActionComposer | None = None,
    ) -> None:
        self._selector = selector or ActionTargetSelector()
        self._composer = composer or DistributionActionComposer(get_llm_provider())

    @staticmethod
    def _require_unprocessed_telegram_target(
        product: ProductProfileView,
        play: DistributionPlayView,
        target: SelectedActionTarget,
    ) -> None:
        """Block duplicate target drafts before any LLM or execution state mutation."""
        if (
            play.platform != DistributionPlatform.TELEGRAM
            or play.action_type
            not in {DistributionActionType.COMMENT, DistributionActionType.REPLY}
            or not target.url
        ):
            return

        def key(url: object) -> str:
            return str(url or "").strip().rstrip("/").split("?", 1)[0].casefold()

        target_key = key(target.url)
        for experiment in distribution_execution_service.list_experiments(product.id):
            try:
                action = distribution_execution_service.get_action(experiment.action_id)
            except KeyError:
                continue
            if (
                action.platform == DistributionPlatform.TELEGRAM
                and action.action_type == play.action_type
                and action.distribution_identity_id == play.selected_identity_id
                and key(action.target_url) == target_key
                and action.status
                in {
                    DistributionActionStatus.PREPARED,
                    DistributionActionStatus.APPROVED,
                    DistributionActionStatus.EXECUTED,
                    DistributionActionStatus.FAILED,
                }
            ):
                raise ValueError(
                    "Telegram target already has a prepared, attempted or executed "
                    "action for this identity; reconcile it instead of drafting again"
                )

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
        self._require_unprocessed_telegram_target(product, play, target)
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
                title=draft.title,
                context_text=draft.context_text,
                content_text=draft.content_text,
                disclosure_included=draft.disclosure_included,
                ai_disclosure_included=draft.ai_disclosure_included,
            ),
        )

    async def auto_prepare_variants(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        destination_url: HttpUrl | None = None,
    ) -> list[PreparedDraftVariant]:
        if play.status != DistributionPlayStatus.READY:
            raise ValueError("Only READY DistributionPlay objects can be auto-prepared")
        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        target = self._selector.select(play, opportunity)
        self._require_unprocessed_telegram_target(product, play, target)
        policy = self._applied_policy(play, opportunity)
        template = next(item for item in TACTIC_CATALOG if item.tactic_id == play.tactic_id)
        briefs = conversion_scenario_planner.variants(
            play=play,
            opportunity=opportunity,
            policy=policy,
            template=template,
        )
        # One Telegram comment/reply draft and one LLM call per target,
        # even if the generic conversion planner suggests alternatives.
        if (
            play.platform == DistributionPlatform.TELEGRAM
            and play.action_type in {
                DistributionActionType.COMMENT, DistributionActionType.REPLY
            }
        ):
            briefs = briefs[:1]
        prepared: list[PreparedDraftVariant] = []
        for brief in briefs:
            draft = await self._composer.compose(
                product=product,
                play=play,
                opportunity=opportunity,
                target=target,
                policy=policy,
                variant=brief,
            )
            plan = distribution_execution_service.prepare(
                product,
                play,
                DistributionExecutionPrepareRequest(
                    destination_url=destination_url,
                    target_url=target.url,
                    title=draft.title,
                    context_text=draft.context_text,
                    content_text=draft.content_text,
                    disclosure_included=draft.disclosure_included,
                    ai_disclosure_included=draft.ai_disclosure_included,
                    conversion_mechanism=brief.conversion_mechanism.value,
                    draft_variant=brief.variant_name,
                    expected_user_next_step=brief.expected_user_next_step,
                ),
            )
            prepared.append(PreparedDraftVariant(brief=brief, plan=plan))
        return prepared

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
