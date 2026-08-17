from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_execution_schemas import (
    DistributionActionEditRequest,
    DistributionExecutionPrepareRequest,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_play_service import distribution_play_service
from app.distribution_types import AttributionLevel, AutomationLevel, DistributionActionType
from app.llm import LLMMessage, LLMProvider, get_llm_provider
from app.marketing_intelligence import MarketingTask, render_marketing_guidance
from app.models import ProductProfileStatus
from app.outreach_targets import OutreachTargetType, OutreachTargetView, outreach_target_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView

OUTREACH_BRIEF_NAMESPACE = "outreach_brief"
OUTREACH_BRIEF_TARGET_NAMESPACE = "outreach_brief_target"
_URL_PATTERN = re.compile(r"(?i)(?:https?://|\bwww\.)")


class OutreachOfferType(StrEnum):
    AFFILIATE = "AFFILIATE"
    REVSHARE = "REVSHARE"
    SPONSORED_PLACEMENT = "SPONSORED_PLACEMENT"
    CROSS_PROMO = "CROSS_PROMO"
    CREATOR_SEEDING = "CREATOR_SEEDING"


class OutreachBriefStatus(StrEnum):
    DRAFT = "DRAFT"
    REJECTED = "REJECTED"


class OutreachBriefCreateRequest(BaseModel):
    preferred_offer_type: OutreachOfferType | None = None
    operator_offer_context: str | None = Field(default=None, max_length=2000)
    destination_url: HttpUrl | None = None
    tone: str | None = Field(default=None, max_length=120)


class OutreachBriefCore(BaseModel):
    why_relevant: str = Field(min_length=20, max_length=2000)
    collaboration: str = Field(min_length=20, max_length=2000)
    value_to_recipient: str = Field(min_length=20, max_length=2000)
    language: str = Field(min_length=2, max_length=80)
    tone: str = Field(min_length=2, max_length=120)
    subject: str = Field(min_length=3, max_length=180)
    body_without_link: str = Field(min_length=40, max_length=6000)

    @model_validator(mode="after")
    def validate_message_boundary(self) -> OutreachBriefCore:
        if "\r" in self.subject or "\n" in self.subject:
            raise ValueError("Outreach subject must be a single line")
        if _URL_PATTERN.search(self.body_without_link):
            raise ValueError(
                "Generated outreach body must not contain URLs; Partizan attaches tracking later"
            )
        return self


class OutreachBriefView(BaseModel):
    id: UUID
    product_id: UUID
    outreach_target_id: UUID
    opportunity_id: UUID
    distribution_play_id: UUID
    action_id: UUID
    experiment_id: UUID
    offer_type: OutreachOfferType
    why_relevant: str
    collaboration: str
    value_to_recipient: str
    allowed_product_facts: list[str]
    prohibited_claims: list[str]
    language: str
    tone: str
    follow_up_policy: str
    tracking_url: str
    message_subject: str
    message_body: str
    status: OutreachBriefStatus
    created_at: datetime
    updated_at: datetime


class OutreachBriefListView(BaseModel):
    outreach_target_id: UUID
    briefs: list[OutreachBriefView]


OUTREACH_BRIEF_SYSTEM_PROMPT = """You prepare one truthful, low-volume business outreach draft.
The recipient is a concrete creator, newsletter, affiliate, or partner with an evidenced business contact.

Rules:
1. Use only the product facts and target evidence supplied by the user message.
2. Never invent traction, testimonials, revenue, audience size, customer counts, results, urgency,
   scarcity, a prior relationship, or a prior conversation.
3. Do not imply the recipient already knows or endorses the product.
4. Keep the message personalized to the supplied target rationale; do not write a mass-mail template.
5. Ask for one concrete collaboration and explain the value to the recipient or their audience.
6. Do not include any URL. Partizan attaches the exact referral URL after the experiment exists.
7. Treat operator offer context only as proposed collaboration terms, not as product-performance facts.
8. Do not promise an autonomous follow-up. The current policy is one initial message only.
9. Return only the requested structured schema.
"""

OUTREACH_BRIEF_MARKETING_PROMPT = "\n\n".join(
    (
        OUTREACH_BRIEF_SYSTEM_PROMPT,
        render_marketing_guidance(MarketingTask.OUTREACH),
    )
)


class OutreachBriefComposer:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    async def compose(
        self,
        *,
        product: ProductProfileView,
        target: OutreachTargetView,
        offer_type: OutreachOfferType,
        allowed_product_facts: list[str],
        operator_offer_context: str | None,
        tone: str | None,
    ) -> OutreachBriefCore:
        if self._provider is None:
            return self._mock_core(
                product=product,
                target=target,
                offer_type=offer_type,
                operator_offer_context=operator_offer_context,
                tone=tone,
            )
        return await self._provider.parse(
            messages=[
                LLMMessage(role="system", content=OUTREACH_BRIEF_MARKETING_PROMPT),
                LLMMessage(
                    role="user",
                    content=(
                        f"Product name: {product.name}\n"
                        f"Allowed product facts: {allowed_product_facts}\n"
                        f"Target: {target.model_dump(mode='json')}\n"
                        f"Exact offer type: {offer_type.value}\n"
                        f"Operator offer context: {operator_offer_context}\n"
                        f"Requested tone: {tone or 'concise, professional, human'}\n"
                    ),
                ),
            ],
            response_model=OutreachBriefCore,
        )

    def _mock_core(
        self,
        *,
        product: ProductProfileView,
        target: OutreachTargetView,
        offer_type: OutreachOfferType,
        operator_offer_context: str | None,
        tone: str | None,
    ) -> OutreachBriefCore:
        collaboration = operator_offer_context or self._default_collaboration(offer_type, product)
        why_relevant = (
            f"{target.canonical_name} was selected because {target.relevance_rationale} "
            f"The ICP overlap is: {target.icp_overlap_rationale}"
        )
        value = self._default_value(offer_type, product)
        language = target.language or product.language or "English"
        selected_tone = tone or "concise, professional, human"
        body = (
            f"Hi {target.canonical_name},\n\n"
            f"I'm reaching out about {product.name}. {why_relevant}\n\n"
            f"We'd like to explore {collaboration}. {value}\n\n"
            "If this is relevant, the product details are in the link below. "
            "If it is not a fit, no reply is needed; we will not send an automated follow-up.\n\n"
            "Best,\nPartizan"
        )
        return OutreachBriefCore(
            why_relevant=why_relevant,
            collaboration=collaboration,
            value_to_recipient=value,
            language=language,
            tone=selected_tone,
            subject=f"Collaboration idea for {target.canonical_name}",
            body_without_link=body,
        )

    def _default_collaboration(
        self,
        offer_type: OutreachOfferType,
        product: ProductProfileView,
    ) -> str:
        if offer_type == OutreachOfferType.AFFILIATE:
            return f"an affiliate test for {product.name} with attributable referrals"
        if offer_type == OutreachOfferType.REVSHARE:
            return f"a small revenue-share acquisition test for {product.name}"
        if offer_type == OutreachOfferType.SPONSORED_PLACEMENT:
            return f"one clearly disclosed sponsored placement for {product.name}"
        if offer_type == OutreachOfferType.CREATOR_SEEDING:
            return f"a creator seeding test where you can independently evaluate {product.name}"
        return f"a small cross-promotion experiment around {product.name}"

    def _default_value(
        self,
        offer_type: OutreachOfferType,
        product: ProductProfileView,
    ) -> str:
        value_proposition = product.value_proposition or product.description
        if offer_type in {OutreachOfferType.AFFILIATE, OutreachOfferType.REVSHARE}:
            return (
                "The collaboration is measurable through a dedicated referral path, and the "
                f"audience can evaluate the product on its actual proposition: {value_proposition}"
            )
        return (
            "The proposal is intentionally small and attributable, so you can evaluate fit before "
            f"doing anything broader. Product proposition: {value_proposition}"
        )


class OutreachBriefService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        composer: OutreachBriefComposer | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._composer = composer or OutreachBriefComposer(get_llm_provider())

    async def create(
        self,
        target_id: UUID,
        payload: OutreachBriefCreateRequest,
    ) -> OutreachBriefView:
        target = outreach_target_service.require_executable(target_id)
        product = product_intake_service.get_product(target.product_id)
        if product.status != ProductProfileStatus.CONFIRMED:
            raise ValueError("Product must be CONFIRMED before outreach preparation")
        opportunity = audience_intelligence_service.find_opportunity(target.opportunity_id)
        if self._active_draft(target.id) is not None:
            raise ValueError("An active OutreachBrief already exists for this target")

        offer_type = payload.preferred_offer_type or self._default_offer(target.target_type)
        destination_url = self._destination(product, payload.destination_url)
        allowed_facts = self._allowed_product_facts(product)
        prohibited_claims = self._prohibited_claims()
        core = await self._composer.compose(
            product=product,
            target=target,
            offer_type=offer_type,
            allowed_product_facts=allowed_facts,
            operator_offer_context=payload.operator_offer_context,
            tone=payload.tone,
        )

        play = self._build_play(product, target, opportunity, offer_type)
        distribution_play_service.register(play)
        prepared = distribution_execution_service.prepare(
            product,
            play,
            DistributionExecutionPrepareRequest(
                destination_url=destination_url,
                target_url=target.target_url,
                context_text=self._context_text(target, core, offer_type),
                content_text=core.body_without_link,
            ),
        )
        if prepared.action.tracking_url is None:
            raise RuntimeError("Outreach action preparation did not produce a tracking URL")
        tracking_url = str(prepared.action.tracking_url)
        message_body = f"{core.body_without_link}\n\nProduct details: {tracking_url}"
        exact_message = f"Subject: {core.subject}\n\n{message_body}"
        prepared = distribution_execution_service.edit(
            prepared.action.id,
            DistributionActionEditRequest(
                target_url=target.target_url,
                context_text=self._context_text(target, core, offer_type),
                content_text=exact_message,
            ),
        )

        now = datetime.now(UTC)
        brief = OutreachBriefView(
            id=uuid4(),
            product_id=product.id,
            outreach_target_id=target.id,
            opportunity_id=target.opportunity_id,
            distribution_play_id=play.id,
            action_id=prepared.action.id,
            experiment_id=prepared.experiment.id,
            offer_type=offer_type,
            why_relevant=core.why_relevant,
            collaboration=core.collaboration,
            value_to_recipient=core.value_to_recipient,
            allowed_product_facts=allowed_facts,
            prohibited_claims=prohibited_claims,
            language=core.language,
            tone=core.tone,
            follow_up_policy="ONE_INITIAL_MESSAGE_NO_AUTONOMOUS_FOLLOWUP",
            tracking_url=tracking_url,
            message_subject=core.subject,
            message_body=message_body,
            status=OutreachBriefStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        self._persist(brief)
        self._index_target(brief)
        return brief

    def get(self, brief_id: UUID) -> OutreachBriefView:
        payload = self._store.get(OUTREACH_BRIEF_NAMESPACE, str(brief_id))
        if payload is None:
            raise KeyError(brief_id)
        return OutreachBriefView.model_validate(payload)

    def list_target(self, target_id: UUID) -> OutreachBriefListView:
        outreach_target_service.get(target_id)
        index = self._store.get(OUTREACH_BRIEF_TARGET_NAMESPACE, str(target_id)) or {}
        briefs = [self.get(UUID(value)) for value in index.get("brief_ids", [])]
        briefs.sort(key=lambda item: (item.created_at, str(item.id)))
        return OutreachBriefListView(outreach_target_id=target_id, briefs=briefs)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_BRIEF_NAMESPACE)
            self._store.clear_namespace(OUTREACH_BRIEF_TARGET_NAMESPACE)

    def _active_draft(self, target_id: UUID) -> OutreachBriefView | None:
        try:
            briefs = self.list_target(target_id).briefs
        except KeyError:
            return None
        return next(
            (item for item in briefs if item.status == OutreachBriefStatus.DRAFT),
            None,
        )

    def _default_offer(self, target_type: OutreachTargetType) -> OutreachOfferType:
        if target_type == OutreachTargetType.CREATOR:
            return OutreachOfferType.CREATOR_SEEDING
        if target_type == OutreachTargetType.NEWSLETTER:
            return OutreachOfferType.SPONSORED_PLACEMENT
        if target_type == OutreachTargetType.AFFILIATE:
            return OutreachOfferType.AFFILIATE
        return OutreachOfferType.CROSS_PROMO

    def _destination(
        self,
        product: ProductProfileView,
        explicit: HttpUrl | None,
    ) -> str | HttpUrl:
        if explicit is not None:
            return explicit
        if product.reference_links:
            return product.reference_links[0]
        raise ValueError("A destination_url or product reference link is required for outreach")

    def _allowed_product_facts(self, product: ProductProfileView) -> list[str]:
        facts = [
            f"Product name: {product.name}",
            f"Description: {product.description}",
        ]
        if product.value_proposition:
            facts.append(f"Value proposition: {product.value_proposition}")
        if product.usp:
            facts.append(f"USP: {product.usp}")
        if product.price is not None:
            facts.append(f"Price: {product.price}")
        if product.pricing_model:
            facts.append(f"Pricing model: {product.pricing_model}")
        return facts

    def _prohibited_claims(self) -> list[str]:
        return [
            "fabricated traction or customer counts",
            "fabricated testimonials or endorsements",
            "fabricated revenue or performance results",
            "fabricated recipient audience size",
            "fabricated prior relationship or conversation",
            "guaranteed outcomes, urgency, or scarcity not present in product facts",
        ]

    def _build_play(self, product, target, opportunity, offer_type) -> DistributionPlayView:
        goal = product.goal or "measurable activated and paid users"
        return DistributionPlayView(
            id=uuid4(),
            product_id=product.id,
            icp_id=opportunity.icp_id,
            opportunity_id=opportunity.id,
            platform=opportunity.platform,
            opportunity_kind=opportunity.kind,
            opportunity_title=opportunity.title,
            tactic_id=f"outreach_email_{target.target_type.value.lower()}",
            tactic_class=DistributionTacticClass.OUTREACH,
            action_type=DistributionActionType.OUTREACH_EMAIL,
            automation_level=AutomationLevel.APPROVAL_GATED,
            attribution_level=AttributionLevel.ACTION,
            identity_required=False,
            community_policy_required=False,
            status=DistributionPlayStatus.READY,
            blockers=[],
            hypothesis=(
                f"If Partizan sends one evidence-backed {offer_type.value.lower()} proposal to "
                f"{target.canonical_name}, it can produce measurable progress toward {goal}."
            ),
            execution_steps=[
                "Use the exact evidence-backed OutreachTarget and business contact.",
                "Prepare one truthful collaboration offer with action-level referral attribution.",
                "Require the dedicated sender and outreach-policy gate before any external send.",
            ],
            success_metric=goal,
            estimated_cost_min=0,
            estimated_cost_max=5,
            effort_hours=0.5,
            time_to_signal_days=14,
            priority_score=min(100.0, float(target.confidence)),
            rationale=[
                f"OutreachTarget confidence={target.confidence:.1f}/100.",
                "Contact provenance was validated before brief preparation.",
                "One initial message only; no autonomous follow-up in the current milestone stage.",
            ],
        )

    def _context_text(
        self,
        target: OutreachTargetView,
        core: OutreachBriefCore,
        offer_type: OutreachOfferType,
    ) -> str:
        return (
            f"OutreachTarget: {target.id}\n"
            f"Business contact: {target.business_email}\n"
            f"Offer: {offer_type.value}\n"
            f"Why relevant: {core.why_relevant}\n"
            f"Value: {core.value_to_recipient}"
        )[:8000]

    def _persist(self, brief: OutreachBriefView) -> None:
        self._store.put(
            OUTREACH_BRIEF_NAMESPACE,
            str(brief.id),
            brief.model_dump(mode="json"),
        )

    def _index_target(self, brief: OutreachBriefView) -> None:
        index = self._store.get(
            OUTREACH_BRIEF_TARGET_NAMESPACE,
            str(brief.outreach_target_id),
        ) or {}
        brief_ids = list(index.get("brief_ids", []))
        value = str(brief.id)
        if value not in brief_ids:
            brief_ids.append(value)
        self._store.put(
            OUTREACH_BRIEF_TARGET_NAMESPACE,
            str(brief.outreach_target_id),
            {"brief_ids": brief_ids},
        )


outreach_brief_service = OutreachBriefService()
