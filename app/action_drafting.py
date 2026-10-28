import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.audience_intelligence_service import audience_intelligence_service
from app.community_voice import (
    COMMUNITY_CREATIVE_GUIDANCE,
    COMMUNITY_CREATIVE_PROMPT_VERSION,
    VoiceVariantId,
    build_voice_context,
)
from app.config import get_settings
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
from app.persisted_voice_writer import (
    PERSISTED_ACTION_SYSTEM_PROMPT,
    PERSISTED_COMMUNITY_PROMPT,
    PERSISTED_COMMUNITY_PROMPT_VERSION,
    writer_voice_payload,
)
from app.project_marketing_memory import (
    ProjectMarketingMemoryService,
    project_marketing_memory_service,
)
from app.reddit_research import action_target_is_fresh
from app.schemas import ProductProfileView
from app.telegram_message_precheck import precheck_telegram_target
from app.telegram_target_excerpt import compact_telegram_target
from app.voice_draft_cache import VoiceDraftCache
from app.voice_profiles import VoiceProfileService, fingerprint, source_snapshot


class DistributionAutoPrepareRequest(BaseModel):
    destination_url: HttpUrl | None = None


class DistributionContentDraft(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    context_text: str = Field(min_length=10, max_length=8000)
    content_text: str = Field(min_length=10, max_length=12000)
    rationale: str = Field(min_length=5, max_length=2000)
    disclosure_included: bool = False
    ai_disclosure_included: bool = False
    voice_profile_version_id: UUID | None = None
    voice_profile_content_hash: str | None = None


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
            if opportunity.platform == DistributionPlatform.TELEGRAM:
                metadata = opportunity.metadata or {}
                if (
                    metadata.get("native_research_status") != "VERIFIED"
                    or metadata.get("community_fit_pass") is not True
                    or metadata.get("action_target_mode") != "STANDALONE_POST"
                    or (metadata.get("surface_capabilities") or {}).get(
                        "standalone_post"
                    ) != "AVAILABLE"
                ):
                    raise ValueError(
                        "Telegram group standalone draft needs verified audience and group surface"
                    )
                target_url = str(metadata.get("action_target_url") or "")
                community_url = str(opportunity.url).rstrip("/")
                if target_url.rstrip("/") != community_url:
                    raise ValueError("Telegram standalone target must be the exact group URL")
                context = "Telegram group: " + str(opportunity.title or "")
                about = str(metadata.get("about") or "").strip()
                if about:
                    context += "\nGroup description: " + about[:1000]
                return SelectedActionTarget(
                    url=target_url, context_text=context,
                    source="telegram_native.group_description",
                )
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


ACTION_DRAFT_SYSTEM_PROMPT = """You are Partizan's marketing message writer.
Prepare ONE concise and relevant message for the selected marketing opportunity.

The constant objective is to attract interested potential customers to the
product. A compelling reaction, relevant opinion, or recognizable author voice
can serve conversion better than generic advice or a polite explanation.

Use the product positioning, intended audience, author's actual identity when
supplied, platform and specific target to shape the message. Adapt naturally
to the language and culture of the community. Avoid generic marketing copy
and unnecessary sentences. Do not force a product mention where it does not
belong.

Write only for the allowed action type and target; do not switch to another
post or initiate unsolicited private messages. Do not invent customer
testimonials, first-hand attendance, product capabilities or other factual
claims, and do not misrepresent the author's relationship to the product.
Observe applicable policy constraints and required commercial disclosures.
Drafting never grants publication permission.

Return only the requested structured output schema.
"""


class DistributionActionComposer:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        memory_service: ProjectMarketingMemoryService | None = None,
        voice_profiles: VoiceProfileService | None = None,
        voice_cache: VoiceDraftCache | None = None,
    ) -> None:
        self._provider = provider
        self._memory = memory_service or project_marketing_memory_service
        self._voice_profiles = voice_profiles
        self._voice_cache = voice_cache

    async def compose(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
        target: SelectedActionTarget,
        policy: CommunityPolicyView | None,
        variant: DraftVariantBrief | None = None,
        voice_variant_id: VoiceVariantId = "adaptive",
        project_id: UUID | None = None,
        voice_version_id: UUID | None = None,
    ) -> DistributionContentDraft:
        self._validate_ai_policy(policy)
        if voice_version_id is not None and not get_settings().persisted_voice_writer_enabled:
            raise RuntimeError("VOICE_TRIAL_NOT_ENABLED")
        if self._provider is None:
            # A v17 paid/canary request must never silently return a mock
            # draft without resolving the active, validated voice.
            if (
                get_settings().persisted_voice_writer_enabled
                and opportunity.platform == DistributionPlatform.TELEGRAM
                and play.action_type in {
                    DistributionActionType.COMMENT,
                    DistributionActionType.REPLY,
                    DistributionActionType.STANDALONE_POST,
                }
            ):
                raise RuntimeError("VOICE_PROVIDER_REQUIRED")
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
        # Generic community-marketing skill guidance was crowding out voice
        # and conversion. Keep it for other action types; use concise,
        # purpose-built organic writing guidance for Telegram.
        memory = self._memory.prompt_context_for_product(
            product.id,
            platform=opportunity.platform.value,
            action_type=play.action_type.value,
        )
        community_prompt = (
            f"Prompt version: {COMMUNITY_CREATIVE_PROMPT_VERSION}\n"
            + COMMUNITY_CREATIVE_GUIDANCE
            if opportunity.platform == DistributionPlatform.TELEGRAM
            and play.action_type in {
                DistributionActionType.COMMENT,
                DistributionActionType.REPLY,
                DistributionActionType.STANDALONE_POST,
            }
            else ""
        )
        use_persisted_voice = bool(
            community_prompt and get_settings().persisted_voice_writer_enabled
        )
        if use_persisted_voice:
            community_prompt = (
                f"Prompt version: {PERSISTED_COMMUNITY_PROMPT_VERSION}\n"
                + PERSISTED_COMMUNITY_PROMPT
            )
        marketing_guidance = (
            "" if community_prompt else render_marketing_guidance(marketing_task)
        )
        if community_prompt:
            # The generic opportunity payload can include dozens of older
            # Telegram messages and full discovery history. Keep only product
            # facts, community identity and the exact selected native target.
            product_data = product.model_dump(mode="json")
            community_metadata = getattr(opportunity, "metadata", {}) or {}
            voice_input = build_voice_context(
                product_data,
                community_title=str(opportunity.title or ""),
                community_about=str(community_metadata.get("about") or ""),
                variant_id=voice_variant_id,
            )
            if use_persisted_voice:
                if project_id is None:
                    raise RuntimeError("VOICE_SCOPE_REQUIRED")
                profiles = self._voice_profiles or VoiceProfileService()
                if voice_version_id is not None:
                    active_version = profiles.get_version(
                        project_id=project_id, product_id=product.id,
                        version_id=voice_version_id,
                    )
                    if active_version.source_fingerprint != fingerprint(
                        source_snapshot(product_data)
                    ):
                        raise RuntimeError("VOICE_SOURCE_CHANGED")
                else:
                    active_version = profiles.active(
                        project_id=project_id, product_id=product.id,
                        product_data=product_data,
                    )
                voice_payload = writer_voice_payload(active_version)
            else:
                voice_payload = voice_input["voice"]
            compacted = compact_telegram_target(target.context_text)
            user_context = json.dumps(
                {
                    "product": voice_input["product_positioning"],
                    "author_voice": voice_payload,
                    "community_title": opportunity.title,
                    "community_about": str(community_metadata.get("about") or "")[:220],
                    "audience_fit_hint": str(
                        community_metadata.get("community_fit_reason") or ""
                    )[:140],
                    "platform": opportunity.platform.value,
                    "action_type": play.action_type.value,
                    "tactic": play.tactic_id,
                    "target_mode": str(community_metadata.get("action_target_mode") or ""),
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
                    "conversion_brief": (
                        self._variant_prompt(variant)
                        if variant else (
                            {
                                "conversion_mechanism": "PROFILE_CLICK",
                                "instruction": (
                                    "Write a useful on-topic message without "
                                    "product references, advertising, URLs or "
                                    "an invitation to visit the author profile. "
                                    "The verified profile CTA is handled separately."
                                ),
                            }
                            if use_persisted_voice
                            and not template.has_direct_product_link
                            and not template.has_product_mention
                            else None
                        )
                    ),
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
        main_prompt = (
            PERSISTED_ACTION_SYSTEM_PROMPT if use_persisted_voice
            else ACTION_DRAFT_SYSTEM_PROMPT
        )
        system_prompt = (
            f"{main_prompt}\n\n{marketing_guidance}"
            f"\n\n{community_prompt}"
        )
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_context),
        ]
        cache = None
        reservation = None
        if use_persisted_voice:
            # The cache is shared by preview/prepare callers using this
            # composer. The persisted request manifest includes the EXACT
            # prompts, selected target, voice, policy and conversion brief.
            cache = self._voice_cache or VoiceDraftCache(store=profiles._store)
            reservation = cache.reserve(
                project_id=project_id, product_id=product.id,
                voice_version_id=active_version.id,
                voice_content_hash=active_version.content_hash,
                system_prompt=system_prompt, user_context=user_context,
            )
            if reservation.cached_draft is not None:
                cached = DistributionContentDraft.model_validate(
                    reservation.cached_draft
                )
                if (
                    cached.voice_profile_version_id != active_version.id
                    or cached.voice_profile_content_hash != active_version.content_hash
                ):
                    raise RuntimeError("VOICE_DRAFT_CACHE_PROVENANCE_CONFLICT")
                return cached
        try:
            with openai_usage_context(
                "action_variant_draft" if variant else "action_auto_prepare", product.id
            ):
                draft = await self._provider.parse(
                    messages=messages, response_model=DistributionContentDraft
                )
        except BaseException:
            # Timeout/cancellation may still be billed. No implicit retry.
            if reservation is not None:
                cache.stop(reservation, status="PROVIDER_OUTCOME_UNKNOWN")
            raise
        try:
            # Creative guidance does not own compliance. Reject drafts that
            # omit mandated disclosures before preparation or approval.
            if policy is not None and policy.disclosure_required and not draft.disclosure_included:
                raise ValueError("Required commercial disclosure missing from draft")
            if (
                "AI_CONTENT_DISCLOSURE_REQUIRED" in self._constraints(policy)
                and not draft.ai_disclosure_included
            ):
                raise ValueError("Required AI-content disclosure missing from draft")
        except Exception:
            if reservation is not None:
                cache.stop(reservation, status="POLICY_REJECTED")
            raise
        if use_persisted_voice:
            draft = draft.model_copy(update={
                "voice_profile_version_id": active_version.id,
                "voice_profile_content_hash": active_version.content_hash,
            })
            cache.complete(reservation, draft.model_dump(mode="json"))
        return draft

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
        project_id: UUID | None = None,
        voice_version_id: UUID | None = None,
    ) -> DistributionExecutionPlanView:
        if play.status != DistributionPlayStatus.READY:
            raise ValueError("Only READY DistributionPlay objects can be auto-prepared")
        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        target = self._selector.select(play, opportunity)
        self._require_unprocessed_telegram_target(product, play, target)
        policy = self._applied_policy(play, opportunity)
        selected_project = self._voice_project_for_draft(
            product.id, play.platform, project_id=project_id
        )
        draft = await self._composer.compose(
            product=product,
            play=play,
            opportunity=opportunity,
            target=target,
            policy=policy,
            project_id=selected_project,
            voice_version_id=voice_version_id,
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
            **(
                {
                    "voice_profile_version_id": draft.voice_profile_version_id,
                    "voice_profile_content_hash": draft.voice_profile_content_hash,
                }
                if draft.voice_profile_version_id is not None else {}
            ),
        )

    async def auto_prepare_variants(
        self,
        *,
        product: ProductProfileView,
        play: DistributionPlayView,
        destination_url: HttpUrl | None = None,
        project_id: UUID | None = None,
        voice_version_id: UUID | None = None,
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
        selected_project = self._voice_project_for_draft(
            product.id, play.platform, project_id=project_id
        )
        prepared: list[PreparedDraftVariant] = []
        for brief in briefs:
            draft = await self._composer.compose(
                product=product,
                play=play,
                opportunity=opportunity,
                target=target,
                policy=policy,
                variant=brief,
                project_id=selected_project,
                voice_version_id=voice_version_id,
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
                **(
                    {
                        "voice_profile_version_id": draft.voice_profile_version_id,
                        "voice_profile_content_hash": draft.voice_profile_content_hash,
                    }
                    if draft.voice_profile_version_id is not None else {}
                ),
            )
            prepared.append(PreparedDraftVariant(brief=brief, plan=plan))
        return prepared

    def _voice_project_for_draft(
        self, product_id: UUID, platform: DistributionPlatform,
        *, project_id: UUID | None,
    ) -> UUID | None:
        if not (
            get_settings().persisted_voice_writer_enabled
            and platform == DistributionPlatform.TELEGRAM
        ):
            return project_id
        profiles = self._composer._voice_profiles or VoiceProfileService()
        if project_id is None:
            return profiles.resolve_unique_project(product_id)
        profiles._verify_project(project_id, product_id)
        return project_id

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
